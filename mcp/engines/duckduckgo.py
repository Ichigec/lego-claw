"""DuckDuckGo движок (без ключей, через `duckduckgo_search`).

DDG-клиент синхронный и блокирующий — гоняем в thread-pool через
`asyncio.to_thread`, чтобы не блокировать event loop MCP-сервера.
"""
from __future__ import annotations

from typing import Any

from . import register
from .base import EngineError, SearchEngine, SearchResult


class DuckDuckGoEngine(SearchEngine):
    name = "duckduckgo"
    description = "DuckDuckGo web search (free, no API key)."

    def available(self) -> bool:
        try:
            import duckduckgo_search  # noqa: F401
        except ImportError:
            return False
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        import asyncio

        from duckduckgo_search import DDGS

        region = kwargs.get("region", "wt-wt")
        safesearch = kwargs.get("safesearch", "moderate")

        def _blocking() -> list[dict[str, Any]]:
            with DDGS() as ddgs:
                return list(
                    ddgs.text(
                        query,
                        max_results=max_results,
                        region=region,
                        safesearch=safesearch,
                    )
                )

        try:
            raw = await asyncio.to_thread(_blocking)
        except Exception as exc:  # noqa: BLE001 — обёрнём в EngineError
            raise EngineError(f"duckduckgo: {exc}") from exc

        return [
            SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("href") or r.get("url") or ""),
                description=str(r.get("body") or ""),
                source=self.name,
                raw=r,
            )
            for r in raw
            if r.get("href") or r.get("url")
        ]


register(DuckDuckGoEngine())
