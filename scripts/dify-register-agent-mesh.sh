#!/usr/bin/env bash
# scripts/dify-register-agent-mesh.sh — публикует 3 наших HTTP-адаптера
# (clawcode-adapter, openhands-adapter, opencode-adapter) как Custom
# Tools в Dify через Console API.
#
# Эквивалент UI-шагов:
#   Studio → Tools → Custom → Create Custom Tool →
#     Schema = OpenAPI URL (http://clawcode-adapter:8790/openapi.json) →
#     Authorization → API Key (Bearer) =  $CLAWCODE_ADAPTER_API_KEY
#
# Идемпотентен — повторный запуск обновит существующие записи по
# имени (`agent-mesh: clawcode`, …). Каждый адаптер должен быть
# доступен в сети `llm-stack-net` (`compose.agents-mesh.yml`).
#
# Запуск из корня репо:  bash scripts/dify-register-agent-mesh.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIFY="$SCRIPT_DIR/.env.dify"
ENV_MAIN="$SCRIPT_DIR/.env"

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
say()  { printf "\033[1;34m→\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

load_selected_env() {
    local env_path="$1"; shift
    [ -f "$env_path" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|\#*) continue ;; esac
        local key="${line%%=*}"
        local value="${line#*=}"
        key="${key%$'\r'}"; value="${value%$'\r'}"
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

load_selected_env "$ENV_DIFY" \
    DIFY_HOST_PORT \
    DIFY_CONSOLE_TOKEN \
    DIFY_ADMIN_EMAIL DIFY_ADMIN_PASSWORD

load_selected_env "$ENV_MAIN" \
    CLAWCODE_ADAPTER_API_KEY \
    OPENHANDS_ADAPTER_API_KEY \
    OPENCODE_ADAPTER_API_KEY \
    MAX_NESTED_AGENT_CALLS

DIFY_HOST_PORT="${DIFY_HOST_PORT:-8090}"
MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}"

[ -n "${CLAWCODE_ADAPTER_API_KEY:-}" ]  || die "CLAWCODE_ADAPTER_API_KEY не задан в .env"
[ -n "${OPENHANDS_ADAPTER_API_KEY:-}" ] || die "OPENHANDS_ADAPTER_API_KEY не задан в .env"
if [ -z "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    warn "OPENCODE_ADAPTER_API_KEY не задан — opencode-adapter будет пропущен."
fi

BASE="http://localhost:$DIFY_HOST_PORT"
CONSOLE="$BASE/console/api"

# ── 1. Получить Console JWT ───────────────────────────────────────────────
TOKEN="${DIFY_CONSOLE_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -n "${DIFY_ADMIN_EMAIL:-}" ] && [ -n "${DIFY_ADMIN_PASSWORD:-}" ]; then
    TOKEN="$(
        curl -fsS -X POST "$CONSOLE/login" \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"$DIFY_ADMIN_EMAIL\",\"password\":\"$DIFY_ADMIN_PASSWORD\",\"language\":\"en-US\",\"remember_me\":true}" \
            | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    print((d.get("data") or {}).get("access_token") or d.get("access_token") or "")
except Exception:
    print("")
'
    )"
fi

if [ -z "$TOKEN" ]; then
    cat >&2 <<EOF
Не удалось получить Console-token Dify.
Получите его в Dify UI → Settings → Profile → API Keys и положите в .env.dify
как DIFY_CONSOLE_TOKEN. Подробнее: docs/dify.md.
EOF
    die "Console token не задан"
fi
AUTH_HDR=(-H "Authorization: Bearer $TOKEN")

# ── 2. Готовим OpenAPI-описания адаптеров ────────────────────────────────
# Dify хранит схему как inline-text. Чтобы избежать race'а на холодный
# контейнер (когда container ещё не отдаёт /openapi.json), и чтобы Dify
# мог сохранить tool без сетевого fetch'а, мы:
#   1) ходим на http://clawcode-adapter:<port>/openapi.json через api-контейнер Dify
#      (он сидит в llm-stack-net, как раз для этого compose.dify.yml
#      добавляет llm-net),
#   2) если получили — скармливаем как schema_type=openapi_content,
#   3) иначе — fall-back на schema_type=openapi и URL.
find_dify_api_container() {
    if [ -n "${DIFY_API_CONTAINER:-}" ]; then
        printf '%s' "$DIFY_API_CONTAINER"
        return 0
    fi
    local n
    for n in dify-api-1 docker-api-1; do
        if docker ps --filter "name=^${n}$" --format '{{.Names}}' 2>/dev/null \
                | grep -q "^${n}$"; then
            printf '%s' "$n"
            return 0
        fi
    done
    docker ps --format '{{.Names}}' 2>/dev/null \
        | grep -E '^(dify|docker)-api-[0-9]+$' \
        | head -1
}

DIFY_API_CONTAINER="$(find_dify_api_container || true)"
if [ -z "$DIFY_API_CONTAINER" ]; then
    warn "Не нашёл Dify api-контейнер (dify-api-1 / docker-api-1). Pre-fetch OpenAPI пропускаем — Dify попробует достать сам."
fi

# Достаём /openapi.json через api-контейнер Dify (в его сетевом
# контексте резолвятся compose-DNS имена адаптеров через llm-stack-net).
# В образе Dify api нет ни wget, ни curl — есть только python3, поэтому
# ходим через urllib.
fetch_openapi_via_api() {
    local svc="$1" port="$2"
    [ -n "$DIFY_API_CONTAINER" ] || return 0
    docker exec "$DIFY_API_CONTAINER" python3 - <<PY 2>/dev/null || true
import sys, socket, urllib.request
socket.setdefaulttimeout(5)
try:
    with urllib.request.urlopen("http://${svc}:${port}/openapi.json") as r:
        sys.stdout.write(r.read().decode("utf-8", errors="replace"))
except Exception:
    pass
PY
}

# helper: создать или обновить custom tool по имени
upsert_tool() {
    local provider_name="$1"      # уникальное имя в namespace workspace
    local label="$2"
    local description="$3"
    local schema_url="$4"
    local bearer="$5"

    local schema_content
    schema_content="$(fetch_openapi_via_api "${schema_url%%:*}" "${schema_url##*:}" || true)"

    local credentials_json
    credentials_json="$(python3 - <<PY
import json
print(json.dumps({
    "auth_type": "api_key",
    "api_key_header": "Authorization",
    "api_key_value": "Bearer $bearer",
    "api_key_header_prefix": "custom",
}))
PY
    )"

    local payload
    payload="$(
        SCHEMA_CONTENT="$schema_content" \
        SCHEMA_URL="http://$schema_url/openapi.json" \
        PROVIDER_NAME="$provider_name" \
        LABEL="$label" \
        DESCRIPTION="$description" \
        CREDENTIALS="$credentials_json" \
        python3 - <<'PY'
import json, os
schema_content = os.environ.get("SCHEMA_CONTENT") or ""
if schema_content:
    schema_type = "openapi"
    schema_field = schema_content
else:
    schema_type = "openapi"
    schema_field = json.dumps({"$ref": os.environ["SCHEMA_URL"]})
print(json.dumps({
    "provider": os.environ["PROVIDER_NAME"],
    "schema_type": schema_type,
    "schema": schema_field,
    "icon": {"content": "🤖", "background": "#FFEAD5"},
    "labels": [],
    "credentials": json.loads(os.environ["CREDENTIALS"]),
    "privacy_policy": "",
    "custom_disclaimer": os.environ["DESCRIPTION"],
}))
PY
    )"

    say "Регистрирую/обновляю Custom Tool: $provider_name"
    HTTP_CODE="$(
        curl -sS -o /tmp/dify-tool-resp.json -w '%{http_code}' \
            -X POST "$CONSOLE/workspaces/current/tool-provider/api/add" \
            "${AUTH_HDR[@]}" \
            -H "Content-Type: application/json" \
            -d "$payload" || true
    )"

    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
        ok "$provider_name: создан (HTTP $HTTP_CODE)"
        return 0
    fi

    # Возможно, такой provider уже существует → пробуем update.
    HTTP_CODE2="$(
        curl -sS -o /tmp/dify-tool-resp.json -w '%{http_code}' \
            -X POST "$CONSOLE/workspaces/current/tool-provider/api/update" \
            "${AUTH_HDR[@]}" \
            -H "Content-Type: application/json" \
            -d "$payload" || true
    )"
    if [ "$HTTP_CODE2" = "200" ] || [ "$HTTP_CODE2" = "201" ]; then
        ok "$provider_name: обновлён (HTTP $HTTP_CODE2)"
        return 0
    fi

    warn "$provider_name: API вернул $HTTP_CODE / $HTTP_CODE2"
    sed 's|^|      |' /tmp/dify-tool-resp.json >&2
    echo >&2
    return 1
}

# ── 3. Регистрируем три адаптера ──────────────────────────────────────────
upsert_tool \
    "agent-mesh-clawcode" \
    "Claw Code" \
    "Делегирует self-contained coding-задачу headless Claw Code (Rust CLI). Один вызов = одна задача. Используйте только когда задача выходит за пределы вашей собственной capability — никогда не вызывайте сами себя (cycle guard, max_nested_agent_calls=$MAX_NESTED_AGENT_CALLS). Endpoints: POST /v1/run (one-shot), POST /v1/sessions + /v1/sessions/{id}/messages (multi-turn)." \
    "clawcode-adapter:8790" \
    "$CLAWCODE_ADAPTER_API_KEY" \
    || warn "clawcode-adapter регистрация не удалась — посмотрите логи docker-api-1"

upsert_tool \
    "agent-mesh-openhands" \
    "OpenHands" \
    "Делегирует self-contained coding-задачу headless OpenHands 1.6 агенту. Каждый вызов спавнит ephemeral runtime sandbox через docker.sock. Один вызов = одна задача. Используйте только когда задача выходит за пределы вашей capability — не вызывайте сами себя (cycle guard, max_nested_agent_calls=$MAX_NESTED_AGENT_CALLS)." \
    "openhands-adapter:8791" \
    "$OPENHANDS_ADAPTER_API_KEY" \
    || warn "openhands-adapter регистрация не удалась"

if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    upsert_tool \
        "agent-mesh-opencode" \
        "opencode" \
        "Делегирует self-contained coding-задачу opencode (https://opencode.ai/), bridged via ACP. Один вызов = одна сессия. Native LSP + terminal внутри workspace. Cycle guard, max_nested_agent_calls=$MAX_NESTED_AGENT_CALLS." \
        "opencode-adapter:8798" \
        "$OPENCODE_ADAPTER_API_KEY" \
        || warn "opencode-adapter регистрация не удалась"
fi

cat <<EOF

==========================================
 Custom Tools зарегистрированы в Dify ($BASE)
   • agent-mesh-clawcode
   • agent-mesh-openhands
$( [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ] && echo "   • agent-mesh-opencode" )

 Куда смотреть в UI:
   Studio → Tools → Custom (видны 2-3 кубика с эмодзи 🤖)
   Studio → Workflow → drag-and-drop tool в любую node

 Импорт примера workflow:
   Studio → Import DSL → examples/dify-workflow-agent-mesh.yml
==========================================
EOF
