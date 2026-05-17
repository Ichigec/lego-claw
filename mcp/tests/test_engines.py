"""Smoke-тесты движков и multi_search.

Сеть не трогаем: все HTTP-вызовы перехватываются через `respx`.
DuckDuckGo-клиент (синхронный) монкипатчим прямо в модуле движка.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

import respx

from engines import ENGINES, build_registry
from engines.base import close_client
from multi import multi_search


@pytest.fixture(autouse=True)
async def reset_http_client():
    """Каждый тест получает свежий `httpx.AsyncClient` без остатков mocks."""
    await close_client()
    yield
    await close_client()


@pytest.fixture
def registry():
    build_registry()
    return ENGINES


# ---------------------------------------------------------------------------
# search_status / list_engines
# ---------------------------------------------------------------------------
def test_build_registry_contains_core_engines(registry):
    """Реестр собирает все ключевые движки."""
    for name in (
        "searxng",
        "duckduckgo",
        "wikipedia",
        "wikidata",
        "arxiv",
        "github",
        "hackernews",
        "stackexchange",
        "crossref",
        "openalex",
        "pypi",
        "npm",
        "brave",
        "google",
        "tavily",
    ):
        assert name in registry, f"{name} missing from registry"


def test_engine_availability_without_keys(registry, monkeypatch):
    """Без env-ключей: SearXNG/Wikipedia/arXiv доступны, Brave/Google/Tavily — нет."""
    for var in ("BRAVE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CX", "TAVILY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

    assert registry["searxng"].available() is True
    assert registry["wikipedia"].available() is True
    assert registry["arxiv"].available() is True

    assert registry["brave"].available() is False
    assert registry["google"].available() is False
    assert registry["tavily"].available() is False


# ---------------------------------------------------------------------------
# SearXNG happy path (httpx-mock)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_searxng_happy_path(monkeypatch):
    """SearXNG парсит JSON-ответ корректно и прокидывает `SEARXNG_URL`."""
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

    build_registry()
    engine = ENGINES["searxng"]

    payload = {
        "query": "mcp",
        "results": [
            {
                "url": "https://example.com/a",
                "title": "Example A",
                "content": "snippet A",
                "score": 1.2,
            },
            {
                "url": "https://example.com/b",
                "title": "Example B",
                "content": "snippet B",
            },
        ],
    }

    with respx.mock(assert_all_called=True) as router:
        router.get("http://searxng:8080/search").mock(
            return_value=httpx.Response(200, json=payload)
        )

        results = await engine.search("mcp", max_results=5)
        assert router.calls.call_count == 1

    assert len(results) == 2
    assert results[0].title == "Example A"
    assert results[0].url == "https://example.com/a"
    assert results[0].description == "snippet A"
    assert results[0].source == "searxng"
    assert results[0].score == 1.2


@pytest.mark.asyncio
async def test_searxng_http_error_raises(monkeypatch):
    """Ошибка 5xx → EngineError (через safe_search)."""
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

    build_registry()
    engine = ENGINES["searxng"]

    from engines.base import EngineError

    with respx.mock() as router:
        router.get("http://searxng:8080/search").mock(
            return_value=httpx.Response(502, text="bad gateway")
        )

        with pytest.raises(EngineError):
            await engine.safe_search("mcp", max_results=5)


# ---------------------------------------------------------------------------
# DuckDuckGo happy path (монкипатч DDGS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duckduckgo_happy_path(monkeypatch):
    """DDG-обёртка маппит hits в `SearchResult`."""
    build_registry()
    engine = ENGINES["duckduckgo"]

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, *args, **kwargs):
            return iter([
                {"title": "T1", "href": "https://a.test/1", "body": "b1"},
                {"title": "T2", "href": "https://a.test/2", "body": "b2"},
            ])

    import engines.duckduckgo as ddg_mod

    class FakeDuckDuckGoModule:
        DDGS = FakeDDGS

    monkeypatch.setitem(__import__("sys").modules, "duckduckgo_search", FakeDuckDuckGoModule)

    results = await engine.search("mcp", max_results=5)
    assert [r.url for r in results] == ["https://a.test/1", "https://a.test/2"]
    assert results[0].source == "duckduckgo"


# ---------------------------------------------------------------------------
# multi_search
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multi_search_merges_and_dedups(monkeypatch):
    """multi_search ходит в выбранные движки и дедуплицирует по URL."""
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

    # DDG монкипатчим на фейк, который возвращает URL, пересекающийся с
    # SearXNG ответом — ожидаем дедуп.
    class FakeDDGS:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def text(self, *a, **k):
            return iter([
                {"title": "DupTitle", "href": "https://dup.test/x", "body": "d"},
                {"title": "Unique DDG", "href": "https://ddg.test/u", "body": "u"},
            ])

    class FakeDuckDuckGoModule:
        DDGS = FakeDDGS

    monkeypatch.setitem(__import__("sys").modules, "duckduckgo_search", FakeDuckDuckGoModule)

    sx_payload = {
        "results": [
            {"url": "https://dup.test/x/", "title": "Dup SearXNG", "content": "sx1"},
            {"url": "https://sx.test/u", "title": "Unique SX", "content": "sx2"},
        ],
    }

    with respx.mock() as router:
        router.get("http://searxng:8080/search").mock(
            return_value=httpx.Response(200, json=sx_payload)
        )
        out = await multi_search(
            "mcp",
            engines=["searxng", "duckduckgo"],
            max_results_per_engine=5,
            total_limit=10,
            per_engine_timeout=5,
        )

    urls = [r["url"] for r in out["results"]]
    assert "https://dup.test/x" in urls or "https://dup.test/x/" in urls
    # Round-robin: первым идёт searxng (по порядку), потом duckduckgo
    assert urls[0].startswith("https://dup.test")  # первый из searxng
    # Убедимся, что dup в выдаче только один раз (после нормализации)
    assert sum(1 for u in urls if u.rstrip("/").endswith("dup.test/x")) == 1
    assert out["engines_used"] == ["searxng", "duckduckgo"]
    assert out["errors"] == {}


@pytest.mark.asyncio
async def test_multi_search_records_engine_errors(monkeypatch):
    """Ошибка одного движка не ломает остальные — она попадает в `errors`."""
    monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

    with respx.mock() as router:
        router.get("http://searxng:8080/search").mock(
            return_value=httpx.Response(500, text="boom")
        )
        out = await multi_search(
            "mcp",
            engines=["searxng"],
            max_results_per_engine=3,
            per_engine_timeout=3,
        )
    assert "searxng" in out["errors"]
    assert out["results"] == []


@pytest.mark.asyncio
async def test_multi_search_unknown_engine():
    """Несуществующий движок → попадает в errors, а не рушит gather."""
    out = await multi_search(
        "mcp",
        engines=["nope-engine"],
        max_results_per_engine=3,
    )
    assert "nope-engine" in out["errors"]
    assert out["engines_used"] == []
