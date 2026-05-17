"""PyPI движок — поиск Python-пакетов через JSON API.

PyPI убрал полноценный поисковый API (XMLRPC endpoint deprecated в
2018-м), поэтому используем `warehouse.pypa.io` pattern: дёргаем
публичный JSON каждой страницы `pypi.org/search/?q=...` нельзя (они
отдают HTML), но `warehouse`-бэкэнд даёт JSON-ресурс для конкретного
пакета — `pypi.org/pypi/<name>/json`. Публичного JSON-search сейчас
нет, поэтому используем сторонний индекс PyPI.dev от pypa:
  - `https://pypi.org/simple/` — полный список имён (слишком большой).

Практичный компромисс: передаём запрос в PyPI Search UI, парсим HTML —
хрупко, но стабильнее, чем undocumented private API'шки. Если надо
надёжнее, подключите SearXNG (там включён movie `pypi`).

Альтернатива: библиотека `pip-search` либо `pipsearch`. Мы используем
`https://pypi.org/simple/` как fallback к «угадай по подстроке».
"""
from __future__ import annotations

from typing import Any

from . import register
from .base import SearchEngine, SearchResult, get_client, http_get_json


class PypiEngine(SearchEngine):
    name = "pypi"
    description = (
        "PyPI package lookup. Резолвит точное имя через pypi.org/pypi/<name>/json; "
        "для подстрочного поиска используйте SearXNG или связку с Google."
    )

    LOOKUP_URL = "https://pypi.org/pypi/{name}/json"
    SIMPLE_URL = "https://pypi.org/simple/"

    def available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        # Шаг 1: пробуем трактовать query как точное имя пакета (самый
        # полезный кейс для LLM: «покажи описание pandas»).
        exact = await self._lookup_exact(query)
        if exact is not None:
            return [exact]

        # Шаг 2: берём полный список имён и ищем подстроку. На сети
        # /simple/ отдаёт ~20MB HTML; фильтруем по подстроке, ограничиваем
        # вывод.
        return await self._substring_search(query, max_results)

    async def _lookup_exact(self, name: str) -> SearchResult | None:
        # Имена пакетов нормализуются по PEP 503 (lowercase, `_`→`-`).
        norm = name.strip().lower().replace("_", "-")
        if not norm or " " in norm:
            return None
        try:
            data = await http_get_json(self.LOOKUP_URL.format(name=norm))
        except Exception:  # noqa: BLE001
            return None
        info = data.get("info") or {}
        url = info.get("home_page") or info.get("project_url") or f"https://pypi.org/project/{norm}/"
        summary = str(info.get("summary") or "")
        version = info.get("version") or ""
        return SearchResult(
            title=f"{info.get('name', norm)} {version}".strip(),
            url=str(url),
            description=summary,
            source=self.name,
            raw=info,
        )

    async def _substring_search(
        self, query: str, max_results: int
    ) -> list[SearchResult]:
        client = await get_client()
        resp = await client.get(self.SIMPLE_URL, headers={"Accept": "application/vnd.pypi.simple.v1+json"})
        resp.raise_for_status()
        try:
            projects = (resp.json().get("projects") or [])
        except Exception:  # noqa: BLE001
            return []

        q = query.lower().replace("_", "-")
        matches = [p.get("name", "") for p in projects if q in (p.get("name") or "").lower()]
        matches.sort(key=lambda n: (len(n), n))
        out: list[SearchResult] = []
        for name in matches[:max_results]:
            out.append(
                SearchResult(
                    title=name,
                    url=f"https://pypi.org/project/{name}/",
                    description="",
                    source=self.name,
                    raw={"name": name},
                )
            )
        return out


register(PypiEngine())
