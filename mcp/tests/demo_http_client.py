#!/usr/bin/env python3
"""Probe the running searchbox MCP HTTP server through the official SSE client.

Зачем: показать, что сервер не только запускается, но и реально отвечает
на JSON-RPC через MCP-протокол по сети (а не только через прямой импорт).

Запускать после `python mcp/server.py --transport http --port 8090`.
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap

from mcp import ClientSession
from mcp.client.sse import sse_client

URL = "http://127.0.0.1:8090/sse"


def fmt(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def render_text(call_result) -> str:
    """MCP CallToolResult → текст / распарсенный JSON, как в LLM-клиенте."""
    blocks = []
    for block in call_result.content:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            blocks.append(json.loads(text))
        except Exception:
            blocks.append(text)
    return blocks[0] if len(blocks) == 1 else blocks


async def main() -> int:
    print(f"[client] connecting via SSE → {URL}")
    async with sse_client(URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            print(f"[client] connected to '{init.serverInfo.name}' "
                  f"v{init.serverInfo.version}; "
                  f"protocol={init.protocolVersion}")

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"[client] tools advertised: {len(tool_names)}")
            print("        " + ", ".join(sorted(tool_names)))
            print()

            print("[client] → list_engines()")
            le = render_text(await session.call_tool("list_engines", {}))
            print(f"        available: {le.get('available')}")
            print()

            print("[client] → search_status()")
            st = render_text(await session.call_tool("search_status", {}))
            print(f"        total={st.get('total')}, "
                  f"available={st.get('available_engines')}")
            print()

            print("[client] → search_wikipedia(query='Anthropic', max_results=2)")
            wiki = render_text(
                await session.call_tool(
                    "search_wikipedia",
                    {"query": "Anthropic", "max_results": 2},
                )
            )
            print(f"        engine={wiki.get('engine')}, "
                  f"hits={len(wiki.get('results', []))}, "
                  f"error={wiki.get('error')}")
            for r in wiki.get("results", []):
                title = textwrap.shorten(r["title"], 80)
                print(f"        · {title} — {r['url']}")
            print()

            print("[client] → search(query='model context protocol', "
                  "engines=['wikipedia','arxiv','searxng'], max_results=2)")
            multi = render_text(
                await session.call_tool(
                    "search",
                    {
                        "query": "model context protocol",
                        "engines": ["wikipedia", "arxiv", "searxng"],
                        "max_results": 2,
                    },
                )
            )
            print(f"        engines_used: {multi.get('engines_used')}")
            print(f"        errors      : {multi.get('errors')}")
            for r in multi.get("results", []):
                title = textwrap.shorten(r["title"], 80)
                print(f"        · [{r['source']}] {title}")
                print(f"            {r['url']}")
            print()

    print("[client] OK — все 4 MCP-tool вызова прошли через HTTP/SSE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
