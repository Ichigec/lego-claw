"""Fan-out по нескольким движкам + merge.

`multi_search` — единая точка для MCP-tool'а `search`. Вызывает все
доступные (или указанные) движки параллельно, соблюдает per-engine
timeout и сливает результаты в round-robin порядке с дедупликацией по
нормализованному URL.

Почему round-robin, а не RRF (Reciprocal Rank Fusion):
  - score'ы разных движков несопоставимы (GitHub stars vs HN points vs
    relevance у SearXNG), RRF с одинаковыми весами даёт странные
    выбросы;
  - round-robin — "fair interleave": каждый движок получает шанс
    первого места, а потом второго, и так далее. Это честнее и
    стабильнее при gather(...) с разным latency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from engines import ENGINES, available_engines, build_registry
from engines.base import EngineError, SearchResult

logger = logging.getLogger("searchbox.multi")


def _normalize_url(url: str) -> str:
    """Дедуп-ключ: lower-case scheme/host, без фрагмента, без trailing `/`."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    scheme = (parts.scheme or "http").lower()
    netloc = parts.netloc.lower()
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, parts.query, ""))


async def _run_engine(
    name: str,
    query: str,
    max_results: int,
    timeout: float,
    **kwargs: Any,
) -> tuple[str, list[SearchResult] | EngineError]:
    """Запуск одного движка с per-engine timeout.

    Возвращает `(name, results|error)` — так проще разложить потом.
    """
    engine = ENGINES.get(name)
    if engine is None:
        return name, EngineError(f"unknown engine: {name}")

    try:
        async with asyncio.timeout(timeout):
            results = await engine.safe_search(
                query, max_results=max_results, **kwargs
            )
        return name, results
    except asyncio.TimeoutError:
        return name, EngineError(f"{name}: timed out after {timeout}s")
    except EngineError as exc:
        return name, exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected error in %s", name)
        return name, EngineError(f"{name}: {exc.__class__.__name__}: {exc}")


def _round_robin_dedup(
    per_engine: dict[str, list[SearchResult]],
    total_limit: int,
) -> list[SearchResult]:
    """Round-robin merge с дедупом по нормализованному URL.

    Берём по одному результату с каждого движка в порядке перечисления,
    пока не наберём `total_limit`. URL'ы, встречавшиеся раньше (с
    другого движка), пропускаем.
    """
    iters = {name: iter(results) for name, results in per_engine.items()}
    seen: set[str] = set()
    out: list[SearchResult] = []
    exhausted: set[str] = set()

    while len(out) < total_limit and len(exhausted) < len(iters):
        for name in list(iters):
            if name in exhausted:
                continue
            try:
                res = next(iters[name])
            except StopIteration:
                exhausted.add(name)
                continue
            key = _normalize_url(res.url)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(res)
            if len(out) >= total_limit:
                break
    return out


async def multi_search(
    query: str,
    engines: Optional[list[str]] = None,
    max_results_per_engine: int = 10,
    total_limit: Optional[int] = None,
    per_engine_timeout: float = 10.0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Асинхронный fan-out по списку движков.

    Args:
        query:                строка запроса.
        engines:              конкретные движки. `None` → все доступные.
        max_results_per_engine: сколько брать с каждого.
        total_limit:          глобальный лимит после merge (default ≈
                              sum(per_engine) ÷ 2 для приличного выхода).
        per_engine_timeout:   timeout в секундах на каждый движок.
        **kwargs:             прокидывается в `engine.search(...)`.

    Returns:
        dict:
          - `query`           — исходный запрос.
          - `engines_used`    — какие движки реально участвовали.
          - `errors`          — `{engine: msg}` для провалившихся.
          - `results`         — merged список `SearchResult` (to_dict()).
          - `per_engine`      — `{engine: [SearchResult, ...]}` до merge.
    """
    build_registry()

    if not engines:
        engines = available_engines()
        if not engines:
            return {
                "query": query,
                "engines_used": [],
                "errors": {},
                "results": [],
                "per_engine": {},
            }
    # Явно запрошенные движки НЕ фильтруем: неизвестное имя должно
    # попасть в `errors`, чтобы пользователь увидел, что движка нет.

    tasks = [
        _run_engine(
            name, query, max_results_per_engine, per_engine_timeout, **kwargs
        )
        for name in engines
    ]
    completed = await asyncio.gather(*tasks, return_exceptions=False)

    per_engine: dict[str, list[SearchResult]] = {}
    errors: dict[str, str] = {}
    for name, outcome in completed:
        if isinstance(outcome, EngineError):
            errors[name] = str(outcome)
            continue
        per_engine[name] = outcome

    if total_limit is None:
        total_limit = max(max_results_per_engine, max_results_per_engine * max(1, len(per_engine) // 2))

    merged = _round_robin_dedup(per_engine, total_limit)

    return {
        "query": query,
        "engines_used": list(per_engine.keys()),
        "errors": errors,
        "results": [r.to_dict() for r in merged],
        "per_engine": {
            name: [r.to_dict() for r in results]
            for name, results in per_engine.items()
        },
    }
