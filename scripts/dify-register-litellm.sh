#!/usr/bin/env bash
# scripts/dify-register-litellm.sh — регистрирует LiteLLM как
# Custom OpenAI-совместимый model provider в Dify через Console API.
#
# Эквивалент UI-шагов:
#   Studio → Settings → Model Provider → OpenAI-API-compatible →
#   Add credentials → Base URL = http://litellm:4000/v1, API Key = …
#
# После запуска в Dify Studio LLM-нодах появится модель
# `qwen3.6-35b-heretic` (или DIFY_LITELLM_DEFAULT_MODEL).
#
# Идемпотентен — повторный запуск обновит credentials. Console-token
# берётся из .env.dify (DIFY_CONSOLE_TOKEN), либо генерируется через
# email/password если они заданы.
#
# Запуск из корня репо:  bash scripts/dify-register-litellm.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIFY="$SCRIPT_DIR/.env.dify"
ENV_MAIN="$SCRIPT_DIR/.env"

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
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
    DIFY_ADMIN_EMAIL DIFY_ADMIN_PASSWORD \
    DIFY_LITELLM_DEFAULT_MODEL \
    DIFY_LITELLM_BASE_URL DIFY_LITELLM_API_KEY

load_selected_env "$ENV_MAIN" \
    LITELLM_API_KEY \
    STACK_DEFAULT_LITELLM_CHAT_MODEL

DIFY_HOST_PORT="${DIFY_HOST_PORT:-8090}"
DIFY_LITELLM_DEFAULT_MODEL="${DIFY_LITELLM_DEFAULT_MODEL:-${STACK_DEFAULT_LITELLM_CHAT_MODEL:-qwen3.6-35b-heretic}}"
DIFY_LITELLM_BASE_URL="${DIFY_LITELLM_BASE_URL:-http://litellm:4000/v1}"
DIFY_LITELLM_API_KEY="${DIFY_LITELLM_API_KEY:-${LITELLM_API_KEY:-sk-local}}"

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
Варианты:
  1) Войдите в http://localhost:$DIFY_HOST_PORT → Settings → Profile → API Keys,
     сгенерируйте «Console API Key», положите в .env.dify:
         DIFY_CONSOLE_TOKEN=app-xxxxxxxxxxxxxxxx
  2) Или задайте DIFY_ADMIN_EMAIL / DIFY_ADMIN_PASSWORD в .env.dify, чтобы
     скрипт сам залогинился (только если admin создан через UI).
EOF
    die "Console token не задан"
fi

AUTH_HDR=(-H "Authorization: Bearer $TOKEN")

# ── 2. Зарегистрировать LiteLLM как OpenAI-API-compatible provider ────────
# В Dify провайдер `openai_api_compatible` принимает endpoint_url, api_key
# и список моделей. Конкретный путь: POST /workspaces/current/model-providers/{provider}/models
say() { printf "\033[1;34m→\033[0m %s\n" "$*"; }

say "Регистрирую модель $DIFY_LITELLM_DEFAULT_MODEL у провайдера openai_api_compatible (Base URL: $DIFY_LITELLM_BASE_URL)"

MODEL_PAYLOAD="$(python3 - <<PY
import json, os
print(json.dumps({
    "model": os.environ["DIFY_LITELLM_DEFAULT_MODEL"],
    "model_type": "llm",
    "credentials": {
        "endpoint_url": os.environ["DIFY_LITELLM_BASE_URL"],
        "api_key": os.environ["DIFY_LITELLM_API_KEY"],
        "mode": "chat",
        "context_size": "32768",
        "max_tokens_to_sample": "4096",
        "function_calling_type": "tool_call",
        "stream_function_calling": "supported",
        "vision_support": "no_support",
        "stream_mode_delimiter": r"\n\n",
        "agent_thought": "supported",
    },
}))
PY
)"

HTTP_CODE="$(
    curl -sS -o /tmp/dify-litellm-resp.json -w '%{http_code}' \
        -X POST "$CONSOLE/workspaces/current/model-providers/langgenius/openai_api_compatible/openai_api_compatible/models" \
        "${AUTH_HDR[@]}" \
        -H "Content-Type: application/json" \
        -d "$MODEL_PAYLOAD" || true
)"

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
    ok "Модель $DIFY_LITELLM_DEFAULT_MODEL зарегистрирована (HTTP $HTTP_CODE)"
elif [ "$HTTP_CODE" = "404" ]; then
    # В разных минор-версиях Dify путь может отличаться. Пробуем
    # backwards-compatible вариант (без префикса langgenius/...).
    HTTP_CODE2="$(
        curl -sS -o /tmp/dify-litellm-resp.json -w '%{http_code}' \
            -X POST "$CONSOLE/workspaces/current/model-providers/openai_api_compatible/models" \
            "${AUTH_HDR[@]}" \
            -H "Content-Type: application/json" \
            -d "$MODEL_PAYLOAD" || true
    )"
    if [ "$HTTP_CODE2" = "200" ] || [ "$HTTP_CODE2" = "201" ]; then
        ok "Модель $DIFY_LITELLM_DEFAULT_MODEL зарегистрирована (через legacy endpoint, HTTP $HTTP_CODE2)"
    else
        warn "API ответил $HTTP_CODE / $HTTP_CODE2 — проверьте совместимость версии Dify (1.13.3 ожидается). Тело ответа:"
        cat /tmp/dify-litellm-resp.json >&2
        echo >&2
        die "Регистрация не удалась"
    fi
else
    warn "API ответил $HTTP_CODE — тело:"
    cat /tmp/dify-litellm-resp.json >&2
    echo >&2
    die "Регистрация не удалась"
fi

cat <<EOF

==========================================
 Provider:    OpenAI-API-compatible (openai_api_compatible)
 Endpoint:    $DIFY_LITELLM_BASE_URL
 Model:       $DIFY_LITELLM_DEFAULT_MODEL  (видна в LLM node как «$DIFY_LITELLM_DEFAULT_MODEL»)
 Tools next:  bash scripts/dify-register-agent-mesh.sh
==========================================
EOF
