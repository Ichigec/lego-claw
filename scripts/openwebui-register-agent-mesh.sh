#!/usr/bin/env bash
# Register the agent-mesh adapters (clawcode-adapter, openhands-adapter) as
# OpenAPI tool servers in a running OpenWebUI instance.
#
# Idempotent — re-running keeps the entries with the same `info.id` and
# replaces their bearer tokens. Mirrors openwebui-register-fsbox.sh /
# openwebui-register-shellbox.sh / openwebui-register-searchbox.sh.
#
# Use this when:
#   * the OpenWebUI data volume already exists (so TOOL_SERVER_CONNECTIONS
#     env pre-seed in stack-start.sh is ignored), or
#   * you want to push a fresh adapter bearer token into the live config
#     without restarting OpenWebUI.
#
# Required env (loaded from .env / .env.openwebui by default):
#   - OPENWEBUI_HOST_PORT
#   - CLAWCODE_ADAPTER_API_KEY     (in .env)
#   - OPENHANDS_ADAPTER_API_KEY    (in .env)
#   - OPENWEBUI_VALIDATE_EMAIL or OPENWEBUI_ADMIN_EMAIL
#   - OPENWEBUI_VALIDATE_PASSWORD or OPENWEBUI_ADMIN_PASSWORD
#
# Fallbacks (if credentials are empty):
#   1. OPENWEBUI_TOKEN — pre-baked JWT; skip signin entirely.
#   2. OPENWEBUI_ADMIN_USER_ID — generate a fresh JWT inside the
#      `open-webui` container via `open_webui.utils.auth.create_token`.
#      Requires docker access and the OpenWebUI container to be running.
#
# Each adapter exposes an OpenAPI surface on the compose-DNS hostname:
#   http://clawcode-adapter:8790   →  POST /v1/run, POST /v1/sessions/...
#   http://openhands-adapter:8791  →  POST /v1/run, POST /v1/sessions/...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENWEBUI_ENV_FILE="$SCRIPT_DIR/.env.openwebui"
MAIN_ENV_FILE="$SCRIPT_DIR/.env"

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
    OPENWEBUI_VALIDATE_PASSWORD

load_selected_env "$MAIN_ENV_FILE" \
    CLAWCODE_ADAPTER_API_KEY \
    OPENHANDS_ADAPTER_API_KEY \
    OPENCODE_ADAPTER_API_KEY \
    MAX_NESTED_AGENT_CALLS

OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

[ -n "${CLAWCODE_ADAPTER_API_KEY:-}" ]  || die "CLAWCODE_ADAPTER_API_KEY не задан (см. .env)"
[ -n "${OPENHANDS_ADAPTER_API_KEY:-}" ] || die "OPENHANDS_ADAPTER_API_KEY не задан (см. .env)"
# opencode-adapter — поднимается рядом с двумя другими. Если ключ пуст —
# пропускаем регистрацию (warn), чтобы скрипт продолжал работать для
# стеков, где opencode пока не нужен.
if [ -z "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    warn "OPENCODE_ADAPTER_API_KEY не задан — opencode-adapter не будет зарегистрирован."
fi

BASE="http://localhost:$OPENWEBUI_HOST_PORT"

# Способ #1 — пред-готовый JWT (например, скопированный из cookie devtools
# или собранный CI-job'ом). Имеет наивысший приоритет.
TOKEN="${OPENWEBUI_TOKEN:-}"

# Способ #2 — есть email/password в .env.openwebui → signin.
if [ -z "$TOKEN" ] && [ -n "$EMAIL" ] && [ -n "$PASSWORD" ]; then
    TOKEN="$(
        curl -fsS -X POST "$BASE/api/v1/auths/signin" \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))'
    )"
fi

# Способ #3 — credentials пусты, но мы можем дотянуться до контейнера
# `open-webui` и сгенерировать JWT внутри него через
# `open_webui.utils.auth.create_token`. Это удобно для свежей установки,
# где админ создан headless'ом, но пароль не лежит в .env.openwebui.
if [ -z "$TOKEN" ]; then
    USER_ID="${OPENWEBUI_ADMIN_USER_ID:-}"
    if [ -z "$USER_ID" ] && command -v docker >/dev/null 2>&1; then
        USER_ID="$(docker exec open-webui python3 -c "
import sqlite3
con = sqlite3.connect('/app/backend/data/webui.db')
row = con.execute(\"SELECT id FROM user WHERE role='admin' ORDER BY created_at LIMIT 1\").fetchone()
print(row[0] if row else '')
" 2>/dev/null || true)"
    fi
    if [ -n "$USER_ID" ] && command -v docker >/dev/null 2>&1; then
        TOKEN="$(docker exec open-webui python3 -c "
import datetime as dt, importlib
mod = importlib.import_module('open_webui.utils.auth')
print(mod.create_token({'id': '$USER_ID'}, expires_delta=dt.timedelta(hours=1)))
" 2>/dev/null || true)"
        [ -n "$TOKEN" ] && ok "JWT сгенерирован через open-webui container (admin id=$USER_ID)"
    fi
fi

if [ -z "$TOKEN" ]; then
    die "Не удалось получить JWT. Задайте OPENWEBUI_ADMIN_EMAIL+PASSWORD (или OPENWEBUI_VALIDATE_*) в .env.openwebui, либо OPENWEBUI_TOKEN, либо запустите при поднятом контейнере open-webui (он сгенерит JWT сам)."
fi

CURRENT_JSON="$(
    curl -fsS "$BASE/api/v1/configs/tool_servers" \
        -H "Authorization: Bearer $TOKEN" 2>/dev/null || echo '{"TOOL_SERVER_CONNECTIONS":[]}'
)"

PAYLOAD="$(
    CURRENT_JSON="$CURRENT_JSON" \
    CLAWCODE_ADAPTER_API_KEY="$CLAWCODE_ADAPTER_API_KEY" \
    OPENHANDS_ADAPTER_API_KEY="$OPENHANDS_ADAPTER_API_KEY" \
    OPENCODE_ADAPTER_API_KEY="${OPENCODE_ADAPTER_API_KEY:-}" \
    MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}" \
    python3 - <<'PY'
import json, os, sys

current = json.loads(os.environ.get("CURRENT_JSON") or '{}')
connections = current.get("TOOL_SERVER_CONNECTIONS") or current.get("tool_server_connections") or []

max_nested = os.environ.get("MAX_NESTED_AGENT_CALLS", "1")

def adapter_entry(adapter_id, url, key, label, description):
    return {
        "type": "openapi",
        "url": url,
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": key,
        "config": {"enable": True},
        "info": {
            "id": adapter_id,
            "name": adapter_id,
            "description": description,
        },
    }

new_entries = [
    adapter_entry(
        "clawcode-adapter",
        "http://clawcode-adapter:8790",
        os.environ.get("CLAWCODE_ADAPTER_API_KEY", ""),
        "Claw Code",
        (
            "Delegate a self-contained coding task to the headless Claw "
            "Code (Rust CLI) agent. ONE call = ONE task. Use only when the "
            "task is outside your own capabilities — never call yourself "
            f"(cycle guard, max_nested_agent_calls={max_nested}). "
            "Endpoints: POST /v1/run (one-shot), POST /v1/sessions + "
            "POST /v1/sessions/{id}/messages (multi-turn)."
        ),
    ),
    adapter_entry(
        "openhands-adapter",
        "http://openhands-adapter:8791",
        os.environ.get("OPENHANDS_ADAPTER_API_KEY", ""),
        "OpenHands",
        (
            "Delegate a self-contained coding task to the headless "
            "OpenHands 1.6 agent. Each call spawns one ephemeral runtime "
            "sandbox via docker.sock. ONE call = ONE task. Use only when "
            "the task is outside your own capabilities — never call "
            f"yourself (cycle guard, max_nested_agent_calls={max_nested}). "
            "Endpoints: POST /v1/run (one-shot), POST /v1/sessions + "
            "POST /v1/sessions/{id}/messages (multi-turn)."
        ),
    ),
]

opencode_key = os.environ.get("OPENCODE_ADAPTER_API_KEY", "")
if opencode_key:
    new_entries.append(
        adapter_entry(
            "opencode-adapter",
            "http://opencode-adapter:8798",
            opencode_key,
            "opencode",
            (
                "Delegate a self-contained coding task to opencode "
                "(https://opencode.ai/) bridged via ACP (JSON-RPC over "
                "`docker exec opencode opencode acp`). ONE call = ONE "
                "ACP session/prompt turn. Native LSP + terminal tools "
                "inside the workspace. Use only when the task is outside "
                "your own capabilities — never call yourself (cycle "
                f"guard, max_nested_agent_calls={max_nested}). "
                "Endpoints: POST /v1/run (one-shot), POST /v1/sessions "
                "+ POST /v1/sessions/{id}/messages (multi-turn)."
            ),
        )
    )

managed = {"clawcode-adapter", "openhands-adapter", "opencode-adapter"}
filtered = [
    c for c in connections
    if (c.get("info") or {}).get("id") not in managed
]
filtered.extend(new_entries)
print(json.dumps({"TOOL_SERVER_CONNECTIONS": filtered}))
PY
)"

curl -fsS -X POST "$BASE/api/v1/configs/tool_servers" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null

if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    ok "agent-mesh tool servers (clawcode-adapter, openhands-adapter, opencode-adapter) зарегистрированы/обновлены в OpenWebUI ($BASE)"
    warn "Не забудьте включить тулы per-user/per-model: chat → ➕ → toggle clawcode-adapter / openhands-adapter / opencode-adapter."
else
    ok "agent-mesh tool servers (clawcode-adapter, openhands-adapter) зарегистрированы/обновлены в OpenWebUI ($BASE)"
    warn "Не забудьте включить тулы per-user/per-model: chat → ➕ → toggle clawcode-adapter / openhands-adapter."
fi
