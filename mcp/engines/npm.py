"""NPM движок — поиск пакетов через официальный registry.

API: `https://registry.npmjs.org/-/v1/search?text=...` — открытый JSON,
ключ не нужен.
"""
from __future__ import annotations

from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class NpmEngine(SearchEngine):
    name = "npm"
    description = "npm registry package search (registry.npmjs.org /-/v1/search)."

    API_URL = "https://registry.npmjs.org/-/v1/search"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "text": query,
            "size": min(max_results, 50),
        }
        data = await http_get_json(self.API_URL, params=params)

        results: list[SearchResult] = []
        for obj in (data.get("objects") or [])[:max_results]:
            pkg = obj.get("package") or {}
            name = str(pkg.get("name") or "")
            if not name:
                continue
            version = pkg.get("version") or ""
            desc = str(pkg.get("description") or "")
            url = str(pkg.get("links", {}).get("npm") or f"https://www.npmjs.com/package/{name}")
            score = (obj.get("score") or {}).get("final")
            results.append(
                SearchResult(
                    title=f"{name} {version}".strip(),
                    url=url,
                    description=desc,
                    source=self.name,
                    score=float(score) if score is not None else None,
                    raw=obj,
                )
            )
        return results


register(NpmEngine())
