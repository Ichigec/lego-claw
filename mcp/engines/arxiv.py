"""arXiv движок — поиск научных препринтов.

arXiv отдаёт Atom XML, парсим через `xml.etree`. Ключ не нужен.
API: `http://export.arxiv.org/api/query?search_query=...`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, get_client


ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivEngine(SearchEngine):
    name = "arxiv"
    description = "arXiv.org scientific preprint search (Atom feed)."

    API_URL = "http://export.arxiv.org/api/query"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": kwargs.get("sort_by", "relevance"),
            "sortOrder": kwargs.get("sort_order", "descending"),
        }
        client = await get_client()
        resp = await client.get(self.API_URL, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        results: list[SearchResult] = []
        for entry in root.findall(f"{ATOM}entry")[:max_results]:
            title_el = entry.find(f"{ATOM}title")
            id_el = entry.find(f"{ATOM}id")
            summary_el = entry.find(f"{ATOM}summary")
            url = (id_el.text or "").strip() if id_el is not None else ""
            if not url:
                continue
            authors = [
                (a.findtext(f"{ATOM}name") or "").strip()
                for a in entry.findall(f"{ATOM}author")
            ]
            author_line = ", ".join(a for a in authors if a)
            summary = (summary_el.text or "").strip() if summary_el is not None else ""
            desc = (author_line + " — " + summary) if author_line else summary
            results.append(
                SearchResult(
                    title=(title_el.text or "").strip() if title_el is not None else "",
                    url=url,
                    description=desc[:500],
                    source=self.name,
                    raw={"id": url},
                )
            )
        return results


register(ArxivEngine())
