#!/usr/bin/env bash
# Register the A2A-compliant `agent-registry` and the `skills-manager`
# services as OpenAPI tool servers in a running OpenWebUI instance.
#
# Idempotent — re-running keeps the entries with the same `info.id` and
# replaces their bearer tokens / descriptions. Mirrors the existing
# openwebui-register-{shellbox,fsbox,searchbox,agent-mesh}.sh scripts.
#
# Use this when:
#   * the OpenWebUI data volume already exists (so the
#     OPENWEBUI_TOOL_SERVER_CONNECTIONS pre-seed in stack-start.sh is
#     ignored), or
#   * you want to push a freshly-rotated bearer into the live config
#     without restarting OpenWebUI.
#
# Required env (loaded from .env / .env.openwebui by default):
#   - OPENWEBUI_HOST_PORT
#   - AGENT_REGISTRY_API_KEY                       (in .env)
#   - SKILLS_MANAGER_API_KEY                       (in .env)
#   - OPENWEBUI_VALIDATE_EMAIL or OPENWEBUI_ADMIN_EMAIL
#   - OPENWEBUI_VALIDATE_PASSWORD or OPENWEBUI_ADMIN_PASSWORD
#
# DNS resolution: both services live on `llm-stack-net` and are reachable
# from the OpenWebUI container under their compose-DNS names:
#   http://agent-registry:8000   →  /v1/agents, /v1/tasks, /v1/agents/{id}/...
#   http://skills-manager:8000   →  /skills, /skills/import, /skills/{id}/attach

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
    AGENT_REGISTRY_API_KEY \
    SKILLS_MANAGER_API_KEY \
    AGENT_REGISTRY_HOST_PORT \
    SKILLS_MANAGER_HOST_PORT

OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

[ -n "$EMAIL" ]    || die "Не задан admin/validate email (.env.openwebui: OPENWEBUI_ADMIN_EMAIL или OPENWEBUI_VALIDATE_EMAIL)"
[ -n "$PASSWORD" ] || die "Не задан admin/validate password"
[ -n "${AGENT_REGISTRY_API_KEY:-}" ] || die "AGENT_REGISTRY_API_KEY не задан (см. .env)"
[ -n "${SKILLS_MANAGER_API_KEY:-}" ] || die "SKILLS_MANAGER_API_KEY не задан (см. .env)"

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
    CURRENT_JSON="$CURRENT_JSON" \
    AGENT_REGISTRY_API_KEY="$AGENT_REGISTRY_API_KEY" \
    SKILLS_MANAGER_API_KEY="$SKILLS_MANAGER_API_KEY" \
    python3 - <<'PY'
import json, os

current = json.loads(os.environ.get("CURRENT_JSON") or '{}')
connections = current.get("TOOL_SERVER_CONNECTIONS") or current.get("tool_server_connections") or []

def entry(server_id, url, key, description):
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
            "id": server_id,
            "name": server_id,
            "description": description,
        },
    }

new_entries = [
    entry(
        "agent-registry",
        "http://agent-registry:8000",
        os.environ.get("AGENT_REGISTRY_API_KEY", ""),
        (
            "A2A Agent Registry: single tool-server fronting every "
            "registered agent adapter (clawcode, openhands, …). Discover "
            "agents via GET /v1/agents (JWS-signed AgentCards). Send/"
            "stream/list/cancel/subscribe Tasks via "
            "/v1/agents/{id}/message:send|stream, /v1/tasks[?agents=*], "
            "/v1/tasks/{agentId}:{taskId}:cancel|subscribe. Prefer this "
            "over wiring each adapter as a separate tool-server."
        ),
    ),
    entry(
        "skills-manager",
        "http://skills-manager:8000",
        os.environ.get("SKILLS_MANAGER_API_KEY", ""),
        (
            "CRUD over .ai/skills/: GET /skills, GET /skills/{id}, "
            "POST /skills, PUT /skills/{id}, DELETE /skills/{id}, "
            "POST /skills/import (aitmpl/url/claude-templates), "
            "POST /skills/{id}/attach (toggle agents list). Each write "
            "bumps the agents' AgentCard.version via /admin/reload "
            "broadcast (Section 8.6 ETag cache invalidation)."
        ),
    ),
]

managed_ids = {e["info"]["id"] for e in new_entries}
filtered = [c for c in connections if (c.get("info") or {}).get("id") not in managed_ids]
filtered.extend(new_entries)
print(json.dumps({"TOOL_SERVER_CONNECTIONS": filtered}))
PY
)"

curl -fsS -X POST "$BASE/api/v1/configs/tool_servers" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null

ok "agent-registry + skills-manager tool servers зарегистрированы/обновлены в OpenWebUI ($BASE)"
warn "Не забудьте включить тулы per-user/per-model: chat → ➕ → toggle agent-registry / skills-manager."
