#!/bin/sh
# searchbox entrypoint.
#
# Starts two processes in one container:
#   1. native MCP HTTP/SSE on :8090  — consumed by OpenHands / Cursor.
#   2. mcpo OpenAPI bridge on :8001 — consumed by OpenWebUI и др. OpenAPI-tool клиентами.
#
# mcpo spawns its OWN stdio-MCP child via `mcpo -- <command>`, so we
# launch /app/server.py twice: once in HTTP mode (backgrounded) and once
# as mcpo's stdio child (in the foreground so the container PID 1 is
# mcpo — simpler signal handling).

set -e

MCPO_PORT="${SEARCHBOX_MCPO_PORT:-8001}"
HTTP_PORT="${SEARCHBOX_HTTP_PORT:-8090}"

if [ -z "${MCPO_API_KEY:-}" ]; then
    echo "searchbox: MCPO_API_KEY is empty — OpenWebUI requests will be rejected" >&2
fi

echo "searchbox: starting native MCP HTTP on :$HTTP_PORT" >&2
python3 /app/server.py --transport http --host 0.0.0.0 --port "$HTTP_PORT" &
HTTP_PID=$!

# If the HTTP server dies, make sure the container exits (so
# compose/restart policy notices). `trap` forwards SIGTERM and friends.
term_handler() {
    kill -TERM "$HTTP_PID" 2>/dev/null || true
    wait "$HTTP_PID" 2>/dev/null || true
    exit 0
}
trap term_handler TERM INT

echo "searchbox: starting mcpo OpenAPI bridge on :$MCPO_PORT" >&2
exec mcpo \
    --host 0.0.0.0 \
    --port "$MCPO_PORT" \
    --api-key "$MCPO_API_KEY" \
    -- python3 /app/server.py --transport stdio
