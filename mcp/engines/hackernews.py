"""Hacker News (Algolia API) движок.

API: `https://hn.algolia.com/api/v1/search?query=...` — бесплатно, без
ключей. Выдаёт и stories, и comments; по умолчанию берём только stories.
"""
from __future__ import annotations

from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class HackerNewsEngine(SearchEngine):
    name = "hackernews"
    description = "Hacker News search via Algolia public API (stories + comments)."

    API_URL = "https://hn.algolia.com/api/v1/search"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        tags = kwargs.get("tags", "story")
        params = {
            "query": query,
            "hitsPerPage": min(max_results, 50),
            "tags": tags,
        }
        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for hit in (data.get("hits") or [])[:max_results]:
            obj_id = hit.get("objectID")
            url = (
                str(hit.get("url") or "")
                or (f"https://news.ycombinator.com/item?id={obj_id}" if obj_id else "")
            )
            if not url:
                continue
            title = str(hit.get("title") or hit.get("story_title") or "")
            author = str(hit.get("author") or "")
            points = hit.get("points")
            desc_parts: list[str] = []
            if author:
                desc_parts.append(f"by {author}")
            if points is not None:
                desc_parts.append(f"{points} points")
            desc = " · ".join(desc_parts)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    description=desc,
                    source=self.name,
                    score=float(points) if points is not None else None,
                    raw=hit,
                )
            )
        return results


register(HackerNewsEngine())
