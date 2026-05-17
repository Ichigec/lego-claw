"""SearXNG движок — приоритетный бэкенд в этой инсталляции.

Ходит в локальный SearXNG-инстанс (`compose.searxng.yml`) по
`http://searxng:8080/search?format=json`. В `docker/searxng/settings.yml`
уже включён JSON-формат и запущен набор upstream-движков (google, bing,
wikipedia, wikidata, github, stackoverflow, arxiv) — поэтому один
запрос сюда == fan-out в несколько движков бесплатно.
"""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, get_client


class SearxngEngine(SearchEngine):
    name = "searxng"
    description = (
        "Локальный SearXNG (meta-search: google/bing/wiki/github/stackoverflow/arxiv). "
        "Источник по умолчанию — приоритетный, без ключей."
    )
    requires = ("SEARXNG_URL",)

    def __init__(self) -> None:
        self._base = os.environ.get("SEARXNG_URL", "http://searxng:8080").rstrip("/")

    @property
    def base_url(self) -> str:
        """Текущий `SEARXNG_URL` (перечитывается динамически для тестов)."""
        return os.environ.get("SEARXNG_URL", self._base).rstrip("/")

    def available(self) -> bool:
        # Включён всегда: даже если контейнер ещё не поднят, сам движок
        # готов сделать попытку; пустой URL → сигнал «выключено вручную».
        return bool(self.base_url)

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": kwargs.get("language", "ru"),
        }
        categories = kwargs.get("categories")
        if categories:
            params["categories"] = categories
        engines = kwargs.get("upstream_engines")
        if engines:
            params["engines"] = ",".join(engines) if isinstance(engines, (list, tuple)) else str(engines)

        client = await get_client()
        # SearXNG у нас запущен с `server.method: GET`, поэтому явно GET.
        resp = await client.get(f"{self.base_url}/search", params=params)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    description=str(item.get("content") or ""),
                    source=self.name,
                    score=item.get("score"),
                    raw=item,
                )
            )
        return results


register(SearxngEngine())
