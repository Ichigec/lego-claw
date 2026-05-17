# searchbox: multi-engine MCP search server for OpenWebUI + OpenHands.
#
# Layout:
#   - python:3.12-slim-bookworm.
#   - Our own MCP server (/app/server.py + /app/engines/...) serves three
#     transports: stdio, Streamable HTTP (/mcp), SSE (/sse) — all at :8090.
#   - mcpo (open-webui/mcpo) wraps the stdio server as an OpenAPI service
#     on :8001, which is what OpenWebUI consumes as an external tool.
#   - Non-root user `searcher` (uid 10002) — distinct from shellbox (10001)
#     and fsbox (1000) to keep ps/audit output unambiguous.
#
# Two ports leave the container:
#   - :8001 → mcpo OpenAPI (OpenWebUI и любой другой OpenAPI-tool клиент).
#   - :8090 → native MCP HTTP/SSE (OpenHands, Cursor, Claude Desktop).
#
# Env keys consumed at runtime (see docker/searchbox/start.sh):
#   MCPO_API_KEY — bearer token OpenWebUI presents on each request.
#   SEARXNG_URL  — defaults to http://searxng:8080 (llm-stack-net DNS).
#   BRAVE_API_KEY / GOOGLE_API_KEY / GOOGLE_CX / TAVILY_API_KEY /
#   GITHUB_TOKEN / STACKEXCHANGE_KEY — enable the matching engines when set.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# MCP SDK + OpenAPI bridge + runtime deps of our engines.
# fastmcp is a secondary import path inside server.py (fallback if the
# canonical `mcp.server.FastMCP` is missing) — keeping it installed lets
# the image boot on both MCP SDK layouts.
RUN pip install --no-cache-dir --break-system-packages \
        "mcp>=1.2" \
        "fastmcp>=0.2" \
        "mcpo>=0.0.16" \
        "httpx>=0.27" \
        "duckduckgo-search>=6.0" \
        "pydantic>=2.5"

RUN groupadd --gid 10002 searcher \
    && useradd --uid 10002 --gid searcher \
        --home /home/searcher --shell /bin/bash searcher \
    && mkdir -p /home/searcher /app \
    && chown -R searcher:searcher /home/searcher /app

# The project layout mirrors the repo: /app/{server.py,multi.py,engines/}
# (no `mcp/__init__.py` on purpose — it would shadow the pip-installed
# MCP SDK package). The start script cd's into /app, so `sys.path` picks
# up `engines/` via the top-level directory.
COPY --chown=searcher:searcher mcp/server.py /app/server.py
COPY --chown=searcher:searcher mcp/multi.py /app/multi.py
COPY --chown=searcher:searcher mcp/engines /app/engines
COPY --chown=searcher:searcher docker/searchbox/start.sh /app/start.sh

USER searcher
WORKDIR /app

ENV HOME=/home/searcher \
    PATH=/home/searcher/.local/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/app \
    SEARCHBOX_MCPO_PORT=8001 \
    SEARCHBOX_HTTP_PORT=8090 \
    SEARXNG_URL=http://searxng:8080

EXPOSE 8001 8090

ENTRYPOINT ["/bin/sh", "/app/start.sh"]
