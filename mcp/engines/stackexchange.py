"""StackExchange (StackOverflow etc.) движок.

API: `https://api.stackexchange.com/2.3/search/advanced`. Работает без
ключа (300 req/day на IP); с `STACKEXCHANGE_KEY` — 10k req/day.
"""
from __future__ import annotations

import html
import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class StackExchangeEngine(SearchEngine):
    name = "stackexchange"
    description = "StackExchange (default site: stackoverflow) advanced search."

    API_URL = "https://api.stackexchange.com/2.3/search/advanced"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        site = str(kwargs.get("site") or "stackoverflow")
        params: dict[str, Any] = {
            "order": "desc",
            "sort": kwargs.get("sort", "relevance"),
            "q": query,
            "site": site,
            "pagesize": min(max_results, 50),
        }
        key = os.environ.get("STACKEXCHANGE_KEY", "").strip()
        if key:
            params["key"] = key

        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for item in (data.get("items") or [])[:max_results]:
            url = str(item.get("link") or "")
            if not url:
                continue
            title = html.unescape(str(item.get("title") or ""))
            score = item.get("score")
            answers = item.get("answer_count")
            is_answered = item.get("is_answered")
            desc_parts: list[str] = []
            if score is not None:
                desc_parts.append(f"score {score}")
            if answers is not None:
                desc_parts.append(f"{answers} answers")
            if is_answered:
                desc_parts.append("accepted")
            desc = " · ".join(desc_parts)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    description=desc,
                    source=self.name,
                    score=float(score) if score is not None else None,
                    raw=item,
                )
            )
        return results


register(StackExchangeEngine())
