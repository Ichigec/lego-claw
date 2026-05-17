#!/usr/bin/env python3
"""Live demo driver for the searchbox MCP server.

Запуск без Docker — дёргаем engines + multi_search напрямую и параллельно
поднимаем HTTP-MCP-инстанс, чтобы продемонстрировать оба пути:

  * прямые вызовы движков (Wikipedia, arXiv, SearXNG, DuckDuckGo);
  * `multi_search` fan-out + dedup;
  * MCP-tool layer через JSON-RPC 2.0 (Streamable HTTP / `/mcp`).

Скрипт ничего не пишет на диск, только печатает и возвращает 0 при успехе.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
sys.path.insert(0, str(MCP_DIR))

# Указываем хостовой адрес SearXNG (он торчит на 8081 из compose.searxng.yml).
os.environ.setdefault("SEARXNG_URL", "http://127.0.0.1:8081")

from engines import ENGINES, available_engines, build_registry  # noqa: E402
from engines.base import close_client  # noqa: E402
from multi import multi_search  # noqa: E402


BAR = "─" * 72


def header(title: str) -> None:
    print()
    print(BAR)
    print(f"  {title}")
    print(BAR)


def print_results(name: str, results: list[dict]) -> None:
    if not results:
        print(f"[{name}] (нет результатов)")
        return
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip().replace("\n", " ")
        url = r.get("url") or ""
        desc = (r.get("description") or "").strip().replace("\n", " ")
        title = textwrap.shorten(title, width=90, placeholder="…")
        desc = textwrap.shorten(desc, width=140, placeholder="…")
        print(f"[{name}] {i:>2}. {title}")
        print(f"        {url}")
        if desc:
            print(f"        — {desc}")


async def demo_registry() -> None:
    header("1. Реестр движков (build_registry + available_engines)")
    build_registry()
    rows = []
    for name, eng in ENGINES.items():
        rows.append(
            (name, "✅" if eng.available() else "  ", ",".join(eng.requires) or "-", eng.description)
        )
    width = max(len(n) for n, *_ in rows)
    print(f"  {'engine'.ljust(width)}  ok  requires           description")
    for name, ok, req, desc in rows:
        print(f"  {name.ljust(width)}  {ok}  {req:<18} {textwrap.shorten(desc, 60)}")
    print()
    print(f"available_engines() → {available_engines()}")


async def demo_single_engine(name: str, query: str, *, max_results: int = 3) -> None:
    header(f"2. Прямой вызов engine.safe_search('{name}')")
    eng = ENGINES.get(name)
    if eng is None:
        print(f"  движка {name} нет в реестре")
        return
    if not eng.available():
        print(f"  движок {name} недоступен (нужны: {eng.requires})")
        return
    res = await eng.safe_search(query, max_results=max_results)
    print_results(name, [r.to_dict() for r in res])


async def demo_multi(query: str) -> None:
    header(f"3. multi_search fan-out — query='{query}'")
    out = await multi_search(
        query=query,
        engines=["searxng", "wikipedia", "arxiv", "duckduckgo"],
        max_results_per_engine=3,
        total_limit=8,
        per_engine_timeout=8.0,
    )
    print(f"engines_used : {out['engines_used']}")
    print(f"errors       : {out['errors']}")
    print(f"merged total : {len(out['results'])}")
    print()
    print_results("merged", out["results"])
    print()
    print("per_engine breakdown:")
    for engine, results in out["per_engine"].items():
        print(f"  · {engine}: {len(results)} hits")


async def demo_mcp_tools() -> None:
    """Поднимаем server.register_tools() и зовём MCP-tool через FastMCP API."""
    header("4. MCP-tool слой: list_engines + search_status")
    from server import _build_mcp, register_tools  # noqa: WPS433

    mcp = _build_mcp()
    register_tools(mcp)

    list_tool = await mcp.call_tool("list_engines", {})
    status_tool = await mcp.call_tool("search_status", {})

    def _payload(call_result):
        for block in call_result:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except Exception:
                    return block.text
        return None

    listed = _payload(list_tool)
    status = _payload(status_tool)

    print("list_engines → доступные движки:")
    for eng in listed.get("available", []):
        print(f"  · {eng}")
    print()
    print(f"search_status → total={status.get('total')}, "
          f"available={status.get('available_engines')}")


async def main() -> int:
    try:
        await demo_registry()
        await demo_single_engine("wikipedia", "model context protocol", max_results=3)
        await demo_single_engine("arxiv", "retrieval augmented generation", max_results=3)
        await demo_single_engine("searxng", "open webui", max_results=3)
        await demo_multi("model context protocol")
        await demo_mcp_tools()
        header("DONE — searchbox MCP server live demo OK")
        return 0
    finally:
        await close_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
