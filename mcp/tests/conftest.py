"""Pytest конфигурация.

Добавляет `mcp/` (родительский каталог `tests/`) в `sys.path`, чтобы
`from engines.base import ...` и `from multi import multi_search`
разрешались без `pip install -e`.

Мы НЕ делаем `mcp/` пакетом (нет `__init__.py`), потому что имя
совпадает с pip-пакетом `mcp` (MCP SDK). Тесты обращаются к движкам
через прямые импорты `engines.<module>`, которые работают благодаря
этому пути.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
