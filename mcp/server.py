#!/usr/bin/env python3
"""MCP Search Server — точка входа.

Поднимает `FastMCP` инстанс, регистрирует в нём tools:

  - `search`              — умный fan-out (все доступные движки).
  - `search_<engine>`     — отдельный tool на каждый движок.
  - `list_engines`        — перечень движков и их readiness.
  - `search_status`       — расширенный статус (какие движки активны
    и какие env-переменные они ожидают).

CLI:
    python server.py                       # stdio (по умолчанию)
    python server.py --transport http      # HTTP на 0.0.0.0:8090
    python server.py --engines searxng,wikipedia  # ограничить реестр
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Optional

# Пакетные импорты: `engines` и `multi` лежат рядом с `server.py`, оба
# используются из root sys.path.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from engines import ENGINES, available_engines, build_registry  # noqa: E402
from engines.base import close_client  # noqa: E402
from multi import multi_search  # noqa: E402

logging.basicConfig(
    level=os.environ.get("SEARCHBOX_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("searchbox")


def _build_mcp(host: str = "0.0.0.0", port: int = 8090) -> Any:
    """Создать `FastMCP` инстанс.

    Мы используем старый импорт `mcp.server.FastMCP` — он есть и в
    официальном `mcp>=1.2`, и в community-пакете `fastmcp`. Host/port
    прокидываются как `**settings`, потому что в `mcp>=1.4` сигнатура
    `FastMCP.run()` уже не принимает их kwargs (бьёт `TypeError`).
    """
    try:
        from mcp.server import FastMCP  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover — fallback path
        from fastmcp import FastMCP  # type: ignore[no-redef]
    return FastMCP(
        "searchbox",
        instructions=(
            "Multi-engine search via MCP. Default tool `search` fans out to "
            "all available engines (SearXNG, Wikipedia, arXiv, GitHub, HN, "
            "StackExchange, Crossref/OpenAlex, PyPI/NPM, DuckDuckGo, + optional "
            "Brave/Google/Tavily) and returns a deduplicated round-robin merge."
        ),
        host=host,
        port=port,
    )


def _engine_metadata(name: str) -> dict[str, Any]:
    engine = ENGINES[name]
    return {
        "name": name,
        "description": engine.description,
        "available": engine.available(),
        "requires": list(engine.requires),
    }


def register_tools(mcp: Any, only: Optional[set[str]] = None) -> None:
    """Зарегистрировать все MCP-tools.

    `only` — если задан, ограничивает реестр движков этим списком
    (для `--engines` и smoke-тестов).
    """
    build_registry()
    if only:
        for name in list(ENGINES):
            if name not in only:
                ENGINES.pop(name, None)

    @mcp.tool()
    async def search(
        query: str,
        engines: Optional[list[str]] = None,
        max_results: int = 10,
        per_engine_timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Умный мульти-поиск: fan-out по всем доступным движкам.

        Args:
            query: текст запроса.
            engines: подмножество движков (None = все доступные).
            max_results: сколько тянуть с каждого движка.
            per_engine_timeout: timeout per engine в секундах.
        """
        return await multi_search(
            query=query,
            engines=engines,
            max_results_per_engine=max_results,
            per_engine_timeout=per_engine_timeout,
        )

    @mcp.tool()
    async def list_engines() -> dict[str, Any]:
        """Перечень всех движков с описанием и readiness."""
        return {
            "engines": [_engine_metadata(name) for name in ENGINES],
            "default_order": list(ENGINES.keys()),
            "available": available_engines(),
        }

    @mcp.tool()
    async def search_status() -> dict[str, Any]:
        """Готовность каждого движка (какие env-переменные нужны)."""
        status: dict[str, dict[str, Any]] = {}
        for name, engine in ENGINES.items():
            status[name] = {
                "available": engine.available(),
                "requires": list(engine.requires),
                "description": engine.description,
            }
        return {
            "engines": status,
            "available_engines": available_engines(),
            "total": len(ENGINES),
        }

    # Индивидуальные tools на каждый движок — LLM может явно попросить
    # `search_github`, `search_arxiv` и так далее.
    for engine_name in list(ENGINES):
        _register_single(mcp, engine_name)


def _register_single(mcp: Any, engine_name: str) -> None:
    """Регистрирует tool `search_<name>` для конкретного движка."""
    engine = ENGINES[engine_name]
    desc = engine.description or f"Search via {engine_name}"

    # Замыкание по engine_name и engine; FastMCP берёт имя функции.
    async def _single_search(query: str, max_results: int = 10) -> dict[str, Any]:
        result = await multi_search(
            query=query,
            engines=[engine_name],
            max_results_per_engine=max_results,
            per_engine_timeout=float(os.environ.get("SEARCHBOX_HTTP_TIMEOUT", "10")),
        )
        return {
            "engine": engine_name,
            "query": query,
            "results": result["per_engine"].get(engine_name, []),
            "error": result["errors"].get(engine_name),
        }

    _single_search.__name__ = f"search_{engine_name}"
    _single_search.__doc__ = desc
    mcp.tool(name=f"search_{engine_name}", description=desc)(_single_search)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP Search Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default=os.environ.get("SEARCHBOX_TRANSPORT", "stdio"),
        help="Transport mode (default: stdio; env: SEARCHBOX_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SEARCHBOX_HOST", "0.0.0.0"),
        help="Bind host for HTTP/SSE transport (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SEARCHBOX_PORT", "8090")),
        help="Port for HTTP/SSE transport (default: 8090).",
    )
    parser.add_argument(
        "--engines",
        default=os.environ.get("SEARCHBOX_ENGINES", ""),
        help="Comma-separated whitelist of engine names (default: all builtins).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)

    only: Optional[set[str]] = None
    if args.engines.strip():
        only = {e.strip() for e in args.engines.split(",") if e.strip()}

    mcp = _build_mcp(host=args.host, port=args.port)
    register_tools(mcp, only=only)

    logger.info(
        "searchbox ready: transport=%s host=%s port=%s engines=%s",
        args.transport,
        args.host,
        args.port,
        ",".join(ENGINES.keys()),
    )

    try:
        if args.transport in ("http", "sse"):
            # `mcp>=1.6` понимает "streamable-http", старее (1.2..1.5) —
            # только "sse". Пробуем streamable-http (endpoint /mcp), на
            # старых версиях fall-back на SSE (endpoint /sse). В обоих
            # случаях host/port уже зашиты в FastMCP.settings.
            chosen: Optional[str] = None
            for candidate in ("streamable-http", "sse"):
                if args.transport == "sse" and candidate != "sse":
                    continue
                try:
                    mcp.run(transport=candidate)
                    chosen = candidate
                    break
                except (ValueError, TypeError) as exc:
                    logger.info("transport %s unsupported (%s); next", candidate, exc)
                    continue
            if chosen is None:
                raise RuntimeError(
                    "no compatible HTTP transport in installed mcp package"
                )
        else:
            mcp.run(transport="stdio")
    finally:
        # mcp.run() для всех транспортов выполняет `anyio.run(...)`, то
        # есть event loop уже закрыт. Открываем свой и закрываем общий
        # httpx-клиент аккуратно (best-effort, без падений на shutdown).
        import asyncio
        try:
            asyncio.run(close_client())
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
