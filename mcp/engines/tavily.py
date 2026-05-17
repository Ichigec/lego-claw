"""Tavily движок — опционально, по `TAVILY_API_KEY`.

Tavily — облачный поиск, оптимизированный под LLM-агенты. Включается
только если задан ключ (в LocalAI-стэке по умолчанию выключен).
"""
from __future__ import annotations

import os
from typing import Any

from . import register
from .base import SearchEngine, SearchResult, http_post_json


class TavilyEngine(SearchEngine):
    name = "tavily"
    description = "Tavily Search (LLM-tuned commercial API, free tier ~1k req/month)."
    requires = ("TAVILY_API_KEY",)

    API_URL = "https://api.tavily.com/search"

    def available(self) -> bool:
        return bool(os.environ.get("TAVILY_API_KEY", "").strip())

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        payload = {
            "api_key": os.environ["TAVILY_API_KEY"],
            "query": query,
            "max_results": max_results,
            "search_depth": kwargs.get("search_depth", "basic"),
            "include_answer": False,
            "include_images": False,
        }
        data = await http_post_json(self.API_URL, json_body=payload)

        results: list[SearchResult] = []
        for item in (data.get("results") or [])[:max_results]:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    description=str(item.get("content") or ""),
                    source=self.name,
                    score=item.get("score"),
                    raw=item,
                )
            )
        return results


register(TavilyEngine())
