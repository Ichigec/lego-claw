#!/usr/bin/env bash
# Register the fsbox tool URL with a running OpenWebUI instance via the admin
# API. Idempotent: re-running keeps the same `info.id=fsbox` entry, replacing
# the previous bearer key. Mirrors openwebui-register-shellbox.sh.
#
# Use this when:
#   * the OpenWebUI data volume already exists (so TOOL_SERVER_CONNECTIONS env
#     pre-seed is ignored), or
#   * you want to push a fresh FSBOX_API_KEY into the live config without
#     restarting OpenWebUI.
#
# Required env (loaded from .env.openwebui by default):
#   - OPENWEBUI_HOST_PORT
#   - FSBOX_API_KEY
#   - OPENWEBUI_VALIDATE_EMAIL or OPENWEBUI_ADMIN_EMAIL
#   - OPENWEBUI_VALIDATE_PASSWORD or OPENWEBUI_ADMIN_PASSWORD

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENWEBUI_ENV_FILE="$SCRIPT_DIR/.env.openwebui"

load_selected_env() {
    local env_path="$1"
    shift
    [ -f "$env_path" ] || return 0

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue
        esac

        local key="${line%%=*}"
        local value="${line#*=}"
        key="${key%$'\r'}"
        value="${value%$'\r'}"

        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac

        for wanted in "$@"; do
            if [ "$key" = "$wanted" ]; then
                export "$key=$value"
                break
            fi
        done
    done < "$env_path"
}

load_selected_env "$OPENWEBUI_ENV_FILE" \
    OPENWEBUI_HOST_PORT \
    OPENWEBUI_ADMIN_EMAIL \
    OPENWEBUI_ADMIN_PASSWORD \
    OPENWEBUI_VALIDATE_EMAIL \
    OPENWEBUI_VALIDATE_PASSWORD \
    FSBOX_API_KEY \
    FSBOX_HOST_PORT

OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

[ -n "$EMAIL" ]    || die "Не задан admin/validate email (.env.openwebui: OPENWEBUI_ADMIN_EMAIL или OPENWEBUI_VALIDATE_EMAIL)"
[ -n "$PASSWORD" ] || die "Не задан admin/validate password"
[ -n "${FSBOX_API_KEY:-}" ] || die "FSBOX_API_KEY не задан"

BASE="http://localhost:$OPENWEBUI_HOST_PORT"

TOKEN="$(
    curl -fsS -X POST "$BASE/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))'
)"
[ -n "$TOKEN" ] || die "OpenWebUI signin не вернул JWT (проверьте credentials и /api/v1/auths/signin)"

CURRENT_JSON="$(
    curl -fsS "$BASE/api/v1/configs/tool_servers" \
        -H "Authorization: Bearer $TOKEN" 2>/dev/null || echo '{"TOOL_SERVER_CONNECTIONS":[]}'
)"

PAYLOAD="$(
    CURRENT_JSON="$CURRENT_JSON" FSBOX_API_KEY="$FSBOX_API_KEY" python3 - <<'PY'
import json, os, sys
current = json.loads(os.environ.get("CURRENT_JSON") or '{}')
connections = current.get("TOOL_SERVER_CONNECTIONS") or current.get("tool_server_connections") or []

key = os.environ.get("FSBOX_API_KEY", "")
new = {
    "type": "openapi",
    "url": "http://fsbox:8001",
    "spec_type": "url",
    "spec": "",
    "path": "openapi.json",
    "auth_type": "bearer",
    "key": key,
    "config": {"enable": True},
    "info": {
        "id": "fsbox",
        "name": "fsbox",
        "description": "Filesystem MCP (read_file/write_file/edit_file/list_directory/search_files/move_file/create_directory) over the shared agent workspace at $FSBOX_WORKSPACE_DIR (default: ~/agent_dev, rw).",
    },
}

filtered = [c for c in connections if (c.get("info") or {}).get("id") != "fsbox"]
filtered.append(new)
print(json.dumps({"TOOL_SERVER_CONNECTIONS": filtered}))
PY
)"

curl -fsS -X POST "$BASE/api/v1/configs/tool_servers" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null

ok "fsbox tool server зарегистрирован/обновлён в OpenWebUI ($BASE)"
warn "Не забудьте включить тул per-user/per-model: chat → ➕ → toggle fsbox."
