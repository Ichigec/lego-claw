"""Brave Search движок (нужен `BRAVE_API_KEY`)."""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class BraveEngine(SearchEngine):
    name = "brave"
    description = "Brave Search API (commercial, free tier ~2k req/month)."
    requires = ("BRAVE_API_KEY",)

    API_URL = "https://api.search.brave.com/res/v1/web/search"

    def available(self) -> bool:
        return bool(os.environ.get("BRAVE_API_KEY", "").strip())

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        headers = {
            "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            "Accept": "application/json",
        }
        params = {"q": query, "count": min(max_results, 20)}
        data = await http_get_json(self.API_URL, params=params, headers=headers)

        results: list[SearchResult] = []
        for item in (data.get("web") or {}).get("results", [])[:max_results]:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    description=str(item.get("description") or ""),
                    source=self.name,
                    raw=item,
                )
            )
        return results


register(BraveEngine())
