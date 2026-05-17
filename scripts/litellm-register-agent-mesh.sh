#!/usr/bin/env bash
# Register the agent-mesh adapters as LiteLLM aliases via the runtime
# `POST /model/new` admin API (LiteLLM v1.83.x: STORE_MODEL_IN_DB=True ⇒
# the entry lives in the litellm-db Postgres and is picked up immediately
# without restarting the proxy).
#
# Idempotent: re-running checks `/v1/models` first and skips models that
# already exist. To force replacement, also pass `FORCE=1` (we delete the
# existing model_info.id and re-create — beta endpoint, behaviour may
# change between minor LiteLLM versions; see
# https://docs.litellm.ai/docs/proxy/model_management).
#
# This script mirrors what we already pin in docker/litellm/config.yaml,
# so a fresh DB will re-converge either way. Use this script when you
# want the registrations live RIGHT NOW without bouncing the litellm
# container (and want a quick smoke for the Admin UI flow described in
# docs/agent-mesh.md → «Track 2: LiteLLM A2A»).
#
# Pre-requisites:
#   * compose.phoenix.yml is up (LiteLLM + Postgres);
#   * compose.agents-mesh.yml is up (clawcode-adapter + openhands-adapter
#     + opencode-adapter);
#   * .env defines LITELLM_API_KEY, CLAWCODE_ADAPTER_API_KEY,
#     OPENHANDS_ADAPTER_API_KEY, OPENCODE_ADAPTER_API_KEY (last one optional
#     — if empty, agent/opencode is skipped).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

load_selected_env() {
    local env_path="$1"
    shift
    [ -f "$env_path" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue ;;
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

load_selected_env "$ENV_FILE" \
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    CLAWCODE_ADAPTER_API_KEY \
    OPENHANDS_ADAPTER_API_KEY \
    OPENCODE_ADAPTER_API_KEY

LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://127.0.0.1:${LITELLM_HOST_PORT}}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
FORCE="${FORCE:-0}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

[ -n "${LITELLM_API_KEY:-}" ]         || die "LITELLM_API_KEY не задан (.env)"
[ -n "${CLAWCODE_ADAPTER_API_KEY:-}" ]  || die "CLAWCODE_ADAPTER_API_KEY не задан (.env)"
[ -n "${OPENHANDS_ADAPTER_API_KEY:-}" ] || die "OPENHANDS_ADAPTER_API_KEY не задан (.env)"

if ! curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" "$LITELLM_BASE_URL/v1/models" >/dev/null; then
    die "LiteLLM на $LITELLM_BASE_URL не отвечает или отклоняет ключ"
fi

EXISTING_MODELS_JSON="$(curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" \
    "$LITELLM_BASE_URL/v1/models")"

model_exists() {
    local name="$1"
    TARGET="$name" python3 -c '
import json, os, sys
target = os.environ["TARGET"]
data = json.load(sys.stdin)
ids = {item.get("id") for item in data.get("data", [])}
sys.exit(0 if target in ids else 1)
' <<<"$EXISTING_MODELS_JSON" >/dev/null 2>&1 && return 0 || return 1
}

register() {
    local model_name="$1"
    local upstream_model="$2"
    local api_base="$3"
    local api_key="$4"
    local description="$5"
    local timeout_s="$6"

    if model_exists "$model_name"; then
        if [ "$FORCE" != "1" ]; then
            ok "$model_name уже зарегистрирован (FORCE=1 чтобы переписать)"
            return 0
        fi
        warn "FORCE=1 → пересоздаю $model_name"
        # `/model/delete` is the supported counterpart in v1.83.x.
        curl -fsS -m 10 -X POST "$LITELLM_BASE_URL/model/delete" \
            -H "Authorization: Bearer $LITELLM_API_KEY" \
            -H "Content-Type: application/json" \
            -d "$(python3 -c 'import json,sys,os; print(json.dumps({"model_name": os.environ["M"]}))' M="$model_name")" \
            >/dev/null || warn "delete для $model_name вернул не-2xx (продолжаю)"
    fi

    local payload
    payload="$(
        MODEL_NAME="$model_name" \
        UPSTREAM_MODEL="$upstream_model" \
        API_BASE="$api_base" \
        API_KEY="$api_key" \
        DESCRIPTION="$description" \
        TIMEOUT_S="$timeout_s" \
        python3 - <<'PY'
import json, os
body = {
    "model_name": os.environ["MODEL_NAME"],
    "litellm_params": {
        "model": os.environ["UPSTREAM_MODEL"],
        "api_base": os.environ["API_BASE"],
        "api_key": os.environ["API_KEY"],
        "timeout": int(os.environ["TIMEOUT_S"]),
        "request_timeout": int(os.environ["TIMEOUT_S"]),
        "max_retries": 0,
    },
    "model_info": {
        "mode": "agent",
        "description": os.environ["DESCRIPTION"],
    },
}
print(json.dumps(body))
PY
    )"

    local code
    code="$(curl -sS -o /tmp/litellm-am-reg.json -w '%{http_code}' \
        -X POST "$LITELLM_BASE_URL/model/new" \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$payload")"
    if [[ "$code" == 2* ]]; then
        ok "$model_name зарегистрирован (HTTP $code)"
    else
        cat /tmp/litellm-am-reg.json >&2 || true
        echo >&2
        die "POST /model/new для $model_name → HTTP $code"
    fi
}

register "agent/clawcode" \
    "openai/clawcode-adapter" \
    "http://clawcode-adapter:8790/v1" \
    "$CLAWCODE_ADAPTER_API_KEY" \
    "Headless Claw Code delegated as a tool (compose.agents-mesh.yml). One call = one task. Cycle guard via X-Agent-Mesh-Depth." \
    1200

register "agent/openhands" \
    "openai/openhands-adapter" \
    "http://openhands-adapter:8791/v1" \
    "$OPENHANDS_ADAPTER_API_KEY" \
    "Headless OpenHands 1.6 delegated as a tool (compose.agents-mesh.yml). Each call spawns an ephemeral runtime sandbox via docker.sock." \
    3600

if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    register "agent/opencode" \
        "openai/opencode-adapter" \
        "http://opencode-adapter:8798/v1" \
        "$OPENCODE_ADAPTER_API_KEY" \
        "opencode (https://opencode.ai/) bridged via ACP (JSON-RPC over docker exec opencode opencode acp stdio) and exposed as A2A. One call = one ACP prompt turn." \
        1200
    ok "agent-mesh aliases (agent/clawcode, agent/openhands, agent/opencode) live в LiteLLM на $LITELLM_BASE_URL"
else
    warn "OPENCODE_ADAPTER_API_KEY не задан — agent/opencode пропущен. Сгенерируйте ключ и перезапустите скрипт."
    ok "agent-mesh aliases (agent/clawcode, agent/openhands) live в LiteLLM на $LITELLM_BASE_URL"
fi
warn "Не забудьте сверить с docker/litellm/config.yaml — если этот файл обновлялся, после рестарта контейнера записи в БД должны соответствовать yaml-источнику."
