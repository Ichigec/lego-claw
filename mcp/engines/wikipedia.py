"""Wikipedia + Wikidata движок.

Использует два endpoint'а MediaWiki API:
  - `{lang}.wikipedia.org/w/api.php?action=query&list=search` — для статей.
  - `www.wikidata.org/w/api.php?action=wbsearchentities` — для сущностей
    (когда пользователь ищет «кто такой X», а не «статья про X»).

Ни ключей, ни аутентификации не требуется — достаточно User-Agent'а,
который уже выставлен в `get_client()` через `engines.base`.
"""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class WikipediaEngine(SearchEngine):
    name = "wikipedia"
    description = "Wikipedia (article search) + Wikidata entities (wbsearchentities)."

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        lang = str(kwargs.get("lang") or os.environ.get("WIKI_LANG") or "ru")
        api_url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(max_results, 50),
            "format": "json",
            "utf8": 1,
            "origin": "*",
        }
        data = await http_get_json(api_url, params=params)

        results: list[SearchResult] = []
        for item in (data.get("query") or {}).get("search", [])[:max_results]:
            title = str(item.get("title") or "")
            if not title:
                continue
            slug = title.replace(" ", "_")
            url = f"https://{lang}.wikipedia.org/wiki/{slug}"
            snippet = str(item.get("snippet") or "")
            # MediaWiki возвращает HTML в snippet — оставляем как есть,
            # LLM справится; агрессивная чистка убирает подсветку match'ей.
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    description=snippet,
                    source=self.name,
                    raw=item,
                )
            )
        return results


class WikidataEngine(SearchEngine):
    name = "wikidata"
    description = "Wikidata entity search (wbsearchentities)."

    API_URL = "https://www.wikidata.org/w/api.php"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "action": "wbsearchentities",
            "search": query,
            "language": kwargs.get("lang", "ru"),
            "uselang": kwargs.get("lang", "ru"),
            "limit": min(max_results, 50),
            "format": "json",
            "origin": "*",
        }
        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for item in data.get("search", [])[:max_results]:
            qid = str(item.get("id") or "")
            if not qid:
                continue
            url = f"https://www.wikidata.org/wiki/{qid}"
            label = str(item.get("label") or qid)
            desc = str(item.get("description") or "")
            results.append(
                SearchResult(
                    title=f"{label} ({qid})",
                    url=url,
                    description=desc,
                    source=self.name,
                    raw=item,
                )
            )
        return results


register(WikipediaEngine())
register(WikidataEngine())
