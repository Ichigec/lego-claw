"""Google Custom Search движок (нужны `GOOGLE_API_KEY` + `GOOGLE_CX`)."""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class GoogleEngine(SearchEngine):
    name = "google"
    description = "Google Custom Search JSON API (free tier ~100 req/day)."
    requires = ("GOOGLE_API_KEY", "GOOGLE_CX")

    API_URL = "https://www.googleapis.com/customsearch/v1"

    def available(self) -> bool:
        return bool(
            os.environ.get("GOOGLE_API_KEY", "").strip()
            and os.environ.get("GOOGLE_CX", "").strip()
        )

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "key": os.environ["GOOGLE_API_KEY"],
            "cx": os.environ["GOOGLE_CX"],
            "q": query,
            # Google CSE жёстко ограничивает `num` в [1..10]
            "num": max(1, min(int(max_results), 10)),
        }
        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for item in (data.get("items") or [])[:max_results]:
            url = str(item.get("link") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    description=str(item.get("snippet") or ""),
                    source=self.name,
                    raw=item,
                )
            )
        return results


register(GoogleEngine())
