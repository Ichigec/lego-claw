"""OpenAlex движок — открытый граф научных публикаций.

API: `https://api.openalex.org/works`. Бесплатно, без ключа; polite
pool активируется через параметр `mailto=` (в User-Agent тоже можно).
"""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class OpenAlexEngine(SearchEngine):
    name = "openalex"
    description = "OpenAlex.org open scholarly graph (works search)."

    API_URL = "https://api.openalex.org/works"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "search": query,
            "per-page": min(max_results, 50),
        }
        mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
        if mailto:
            params["mailto"] = mailto

        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            url = str(item.get("doi") or item.get("id") or "")
            if not url:
                continue
            title = str(item.get("title") or item.get("display_name") or "").strip()
            year = item.get("publication_year")
            authorships = item.get("authorships") or []
            authors = [
                (a.get("author") or {}).get("display_name", "") for a in authorships[:5]
            ]
            desc_parts: list[str] = []
            if year:
                desc_parts.append(str(year))
            authors_str = ", ".join(a for a in authors if a)
            if authors_str:
                desc_parts.append(authors_str)
            desc = " · ".join(desc_parts)
            results.append(
                SearchResult(
                    title=title or "(untitled)",
                    url=url,
                    description=desc,
                    source=self.name,
                    score=item.get("relevance_score"),
                    raw=item,
                )
            )
        return results


register(OpenAlexEngine())
