#!/usr/bin/env bash
# Добавить алиас модели в LiteLLM proxy без рестарта контейнера (запись в БД при STORE_MODEL_IN_DB=True).
# Контракт API сверен с документацией LiteLLM Model Management для линейки v1.83.x:
#   POST {base}/model/new
#   https://docs.litellm.ai/docs/proxy/model_management
#
# Образ стека: ghcr.io/berriai/litellm-database:v1.83.7-stable (compose.phoenix.yml).
# Master key в контейнере = LITELLM_MASTER_KEY, у нас проброшен из LITELLM_API_KEY в .env.
#
# Пример — второй model_name с тем же upstream, что openai/qwen3.6-35b-heretic (llama.cpp через env):
#   export LITELLM_API_KEY=sk-...
#   NEW_MODEL_NAME='openai/qwen3.6-35b-heretic-alt' \
#     UPSTREAM_LITELLM_MODEL='openai/qwen3.6-35b-heretic' \
#     bash scripts/litellm-add-model-curl.sh
#
# Переменные:
#   LITELLM_BASE_URL  — без завершающего слэша, по умолчанию http://127.0.0.1:${LITELLM_HOST_PORT:-4000}
#   LITELLM_API_KEY   — обязательно (Bearer = master key)
#   NEW_MODEL_NAME    — обязательно: новый алиас в каталоге прокси
#   UPSTREAM_LITELLM_MODEL — опционально, по умолчанию openai/qwen3.6-35b-heretic (поле litellm_params.model)
#   LLAMA_CPP_API_BASE_REF / LLAMA_CPP_API_KEY_REF — строки для api_base/api_key в теле (по умолчанию os.environ/...)
set -euo pipefail

LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://127.0.0.1:${LITELLM_HOST_PORT}}"
LITELLM_API_KEY="${LITELLM_API_KEY:-}"
NEW_MODEL_NAME="${NEW_MODEL_NAME:-}"
UPSTREAM_LITELLM_MODEL="${UPSTREAM_LITELLM_MODEL:-openai/qwen3.6-35b-heretic}"
LLAMA_CPP_API_BASE_REF="${LLAMA_CPP_API_BASE_REF:-os.environ/LLAMA_CPP_API_BASE}"
LLAMA_CPP_API_KEY_REF="${LLAMA_CPP_API_KEY_REF:-os.environ/LLAMA_CPP_API_KEY}"

if [[ -z "$LITELLM_API_KEY" ]]; then
    echo "error: set LITELLM_API_KEY (same value as in .env → LITELLM_MASTER_KEY in container)" >&2
    exit 1
fi

if [[ -z "$NEW_MODEL_NAME" ]]; then
    echo "error: set NEW_MODEL_NAME to the proxy alias you want to register (must be unique)" >&2
    exit 1
fi

endpoint="${LITELLM_BASE_URL%/}/model/new"

# jq avoids shell-escaping bugs in JSON; fallback to Python stdlib.
payload="$(
    NEW_MODEL_NAME="$NEW_MODEL_NAME" \
        UPSTREAM_LITELLM_MODEL="$UPSTREAM_LITELLM_MODEL" \
        LLAMA_CPP_API_BASE_REF="$LLAMA_CPP_API_BASE_REF" \
        LLAMA_CPP_API_KEY_REF="$LLAMA_CPP_API_KEY_REF" \
        python3 - <<'PY'
import json, os

body = {
    "model_name": os.environ["NEW_MODEL_NAME"],
    "litellm_params": {
        "model": os.environ["UPSTREAM_LITELLM_MODEL"],
        "api_base": os.environ["LLAMA_CPP_API_BASE_REF"],
        "api_key": os.environ["LLAMA_CPP_API_KEY_REF"],
    },
}
print(json.dumps(body))
PY
)"

resp_file="$(mktemp)"
trap 'rm -f "$resp_file"' EXIT

code_http="$(curl -sS -o "$resp_file" -w '%{http_code}' \
    -X POST "$endpoint" \
    -H 'accept: application/json' \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${LITELLM_API_KEY}" \
    -d "$payload")"

if command -v jq >/dev/null 2>&1; then
    jq . "$resp_file" 2>/dev/null || cat "$resp_file"
else
    cat "$resp_file"
fi
echo >&2
echo "HTTP $code_http POST $endpoint" >&2

if [[ "$code_http" != 2* ]]; then
    exit 1
fi
