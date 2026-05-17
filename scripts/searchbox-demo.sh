#!/usr/bin/env bash
#
# searchbox MCP — full live demo (no Docker required).
#
# Что показывает:
#   1. pytest smoke-suite (8/8 без сети, все на mock'ах)
#   2. прямые вызовы движков: Wikipedia, arXiv, SearXNG, DuckDuckGo
#      + multi_search fan-out с дедупом
#   3. HTTP/SSE-транспорт: поднимает FastMCP на 127.0.0.1:8090,
#      коннектится через официальный mcp.client.sse и зовёт tools
#      по-настоящему (initialize → list_tools → call_tool)
#
# Требует: SearXNG поднят на 127.0.0.1:8081 (compose.searxng.yml).
# Если не поднят — соответствующие проверки получат engine error,
# остальные 11 движков продолжат работать.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source .venv/bin/activate

export SEARXNG_URL="${SEARXNG_URL:-http://127.0.0.1:8081}"
export SEARCHBOX_LOG_LEVEL="${SEARCHBOX_LOG_LEVEL:-WARNING}"

bar() { printf '\n\033[1;36m%s\033[0m\n' "════════════════════════════════════════════════════════════════════════"; }
hdr() { bar; printf '  \033[1;36m%s\033[0m\n' "$1"; bar; }

hdr "[1/3] pytest smoke-suite (mocked, no network)"
( cd mcp && python -m pytest -q )

hdr "[2/3] direct-import demo: engines + multi_search (живые сети)"
python mcp/tests/demo.py

hdr "[3/3] HTTP transport: FastMCP /sse + mcp.client.sse"

LOGFILE="$(mktemp -t searchbox-demo.XXXXXX.log)"
python mcp/server.py --transport http --host 127.0.0.1 --port 8090 \
    > "$LOGFILE" 2>&1 &
SERVER_PID=$!

# uvicorn перехватывает SIGTERM и иногда висит на graceful-shutdown — после
# 1.5s бьём SIGKILL. wait не блокируем, чтобы скрипт всегда завершался.
cleanup() {
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8; do
            kill -0 "$SERVER_PID" 2>/dev/null || break
            sleep 0.2
        done
        kill -KILL "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$LOGFILE"
}
trap cleanup EXIT

# Ждём, пока uvicorn реально откроет порт (до 10 секунд).
for _ in $(seq 1 50); do
    if ss -ltn 2>/dev/null | awk '$4 ~ /:8090$/ {found=1} END{exit !found}'; then
        break
    fi
    sleep 0.2
done

echo "  HTTP server pid=$SERVER_PID listening on http://127.0.0.1:8090/sse"
echo "  (server log → $LOGFILE)"
echo

python mcp/tests/demo_http_client.py

bar
echo "  ✅ DEMO COMPLETED — searchbox MCP server полностью живой"
bar
