"""GitHub search движок.

Использует GitHub Search API:
  - `api.github.com/search/repositories` — по умолчанию.
  - `api.github.com/search/code` — если `mode="code"` в kwargs (нужен токен).

Без токена GitHub даёт 10 запросов/минуту; с токеном (`GITHUB_TOKEN`) — 30.
"""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_get_json


class GithubEngine(SearchEngine):
    name = "github"
    description = "GitHub repository/code search. Optional GITHUB_TOKEN for higher rate limits."

    BASE_URL = "https://api.github.com/search"

    def available(self) -> bool:
        return True

    def _auth_headers(self) -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        mode = str(kwargs.get("mode") or "repositories")
        if mode not in {"repositories", "code", "issues", "users"}:
            mode = "repositories"

        params = {
            "q": query,
            "per_page": min(max_results, 50),
        }
        data = await http_get_json(
            f"{self.BASE_URL}/{mode}",
            params=params,
            headers=self._auth_headers(),
        )

        results: list[SearchResult] = []
        for item in (data.get("items") or [])[:max_results]:
            if mode == "repositories":
                url = str(item.get("html_url") or "")
                title = str(item.get("full_name") or "")
                desc = str(item.get("description") or "")
                score = float(item.get("stargazers_count") or 0) or None
            elif mode == "code":
                url = str(item.get("html_url") or "")
                repo = (item.get("repository") or {}).get("full_name", "")
                title = f"{repo}:{item.get('path', '')}"
                desc = str(item.get("name") or "")
                score = None
            elif mode == "issues":
                url = str(item.get("html_url") or "")
                title = str(item.get("title") or "")
                desc = str(item.get("body") or "")[:300]
                score = None
            else:  # users
                url = str(item.get("html_url") or "")
                title = str(item.get("login") or "")
                desc = str(item.get("type") or "")
                score = float(item.get("score") or 0) or None
            if not url:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    description=desc,
                    source=self.name,
                    score=score,
                    raw=item,
                )
            )
        return results


register(GithubEngine())
