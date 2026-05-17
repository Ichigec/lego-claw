"""Crossref движок — поиск научных публикаций по DOI/названиям."""
from __future__ import annotations

from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class CrossrefEngine(SearchEngine):
    name = "crossref"
    description = "Crossref.org works search (DOI/title/author metadata)."

    API_URL = "https://api.crossref.org/works"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "query": query,
            "rows": min(max_results, 50),
        }
        data = await http_get_json(self.API_URL, params=params)

        items = ((data.get("message") or {}).get("items")) or []
        results: list[SearchResult] = []
        for item in items[:max_results]:
            doi = str(item.get("DOI") or "")
            url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
            if not url:
                continue
            title_list = item.get("title") or []
            title = (title_list[0] if title_list else "") or doi or "(untitled)"
            authors_raw = item.get("author") or []
            authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in authors_raw
            ]
            year = ""
            issued = (item.get("issued") or {}).get("date-parts") or []
            if issued and issued[0]:
                year = str(issued[0][0] or "")
            desc = ", ".join(filter(None, authors[:5]))
            if year:
                desc = f"{year} · {desc}" if desc else year
            results.append(
                SearchResult(
                    title=str(title).strip(),
                    url=url,
                    description=desc,
                    source=self.name,
                    raw=item,
                )
            )
        return results


register(CrossrefEngine())
