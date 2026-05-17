"""Реестр поисковых движков.

`ENGINES` — словарь `name -> SearchEngine` с авто-регистрацией при импорте
модуля. Добавление нового движка: создать файл `engines/<name>.py`,
унаследоваться от `SearchEngine`, и зарегистрировать его через
`register(<cls>())` в конце файла.

`build_registry()` — функция, которую зовёт `server.py` при старте. Она
гарантирует, что все встроенные движки импортированы (и значит
зарегистрированы), и возвращает актуальный словарь.
"""
from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import SearchEngine

logger = logging.getLogger("searchbox.registry")

ENGINES: dict[str, "SearchEngine"] = {}

# Порядок имеет значение: это дефолтный порядок fan-out в `multi_search`
# и round-robin merge. Локальные/free-tier бэкенды идут первыми.
_BUILTIN_ENGINES: tuple[str, ...] = (
    "searxng",
    "duckduckgo",
    "wikipedia",
    "arxiv",
    "github",
    "hackernews",
    "stackexchange",
    "crossref",
    "openalex",
    "pypi",
    "npm",
    "brave",
    "google",
    "tavily",
)


def register(engine: "SearchEngine") -> None:
    """Добавить движок в реестр. Идемпотентно (перезапись по имени)."""
    if not engine.name:
        raise ValueError(f"engine {engine!r} has empty name")
    ENGINES[engine.name] = engine
    logger.debug("registered engine: %s", engine.name)


def build_registry() -> dict[str, "SearchEngine"]:
    """Импортирует все встроенные движки и возвращает `ENGINES`.

    Идемпотентна: повторный вызов не создаёт дубликатов.
    """
    for mod in _BUILTIN_ENGINES:
        try:
            importlib.import_module(f"engines.{mod}")
        except Exception:  # noqa: BLE001
            logger.exception("failed to import engine module: %s", mod)
    return ENGINES


def available_engines() -> list[str]:
    """Список движков, у которых `available()` вернул True."""
    return [name for name, eng in ENGINES.items() if eng.available()]
