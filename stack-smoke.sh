#!/usr/bin/env bash
# Smoke-tests для связки LM Studio/llama-server → LiteLLM → Phoenix → LocalAI → OpenWebUI.
# Выполнять после stack-start.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.llamacpp}"
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

load_selected_env "$ENV_FILE" \
    LLAMA_HOST_PORT \
    LMSTUDIO_HOST_PORT \
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    PHOENIX_HOST_PORT \
    PHOENIX_PROJECT_NAME \
    LOCALAI_HOST_PORT \
    LOCALAI_ENABLE_QWEN36 \
    LLM_BACKEND \
    LMSTUDIO_MODEL_ID \
    PUBLIC_MODEL_ALIAS

load_selected_env "$ENV_OVERRIDE_FILE" \
    LLAMA_HOST_PORT \
    LMSTUDIO_HOST_PORT \
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    PHOENIX_HOST_PORT \
    PHOENIX_PROJECT_NAME \
    LOCALAI_HOST_PORT \
    LOCALAI_ENABLE_QWEN36 \
    LLM_BACKEND \
    LMSTUDIO_MODEL_ID \
    PUBLIC_MODEL_ALIAS

load_selected_env "$OPENWEBUI_ENV_FILE" \
    OPENWEBUI_ENABLED \
    OPENWEBUI_HOST_PORT \
    OPENWEBUI_DEFAULT_MODEL \
    OPENWEBUI_VALIDATE_MODEL \
    OPENWEBUI_VALIDATE_EMAIL \
    OPENWEBUI_VALIDATE_PASSWORD \
    OPENWEBUI_ADMIN_EMAIL \
    OPENWEBUI_ADMIN_PASSWORD \
    OPENWEBUI_AUDIO_STT_MODEL \
    OPENWEBUI_AUDIO_TTS_MODEL \
    SEARXNG_HOST_PORT \
    JUPYTER_TOKEN \
    JUPYTER_HOST \
    JUPYTER_PORT \
    SHELLBOX_HOST_PORT \
    SHELLBOX_API_KEY

load_selected_env "$ENV_FILE" \
    CLAWCODE_ADAPTER_API_KEY \
    OPENHANDS_ADAPTER_API_KEY \
    OPENCODE_ADAPTER_API_KEY \
    CLAWCODE_ADAPTER_HOST_PORT \
    OPENHANDS_ADAPTER_HOST_PORT \
    OPENCODE_ADAPTER_HOST_PORT \
    MAX_NESTED_AGENT_CALLS

LLAMA_HOST_PORT="${LLAMA_HOST_PORT:-8090}"
LMSTUDIO_HOST_PORT="${LMSTUDIO_HOST_PORT:-1234}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
PHOENIX_HOST_PORT="${PHOENIX_HOST_PORT:-6006}"
PHOENIX_PROJECT_NAME="${PHOENIX_PROJECT_NAME:-qwen3.6-heretic}"
LOCALAI_HOST_PORT="${LOCALAI_HOST_PORT:-8180}"
LOCALAI_ENABLE_QWEN36="${LOCALAI_ENABLE_QWEN36:-0}"
LLM_BACKEND="${LLM_BACKEND:-lmstudio}"
LMSTUDIO_MODEL_ID="${LMSTUDIO_MODEL_ID:-tvall43-qwen3.6-35b-a3b-heretic}"
PUBLIC_MODEL_ALIAS="${PUBLIC_MODEL_ALIAS:-qwen3.6-35b-heretic}"
OPENWEBUI_ENABLED="${OPENWEBUI_ENABLED:-1}"
OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
OPENWEBUI_DEFAULT_MODEL="${OPENWEBUI_DEFAULT_MODEL:-$PUBLIC_MODEL_ALIAS}"
OPENWEBUI_VALIDATE_MODEL="${OPENWEBUI_VALIDATE_MODEL:-$OPENWEBUI_DEFAULT_MODEL}"
OPENWEBUI_VALIDATE_EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
OPENWEBUI_VALIDATE_PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"
OPENWEBUI_AUDIO_STT_MODEL="${OPENWEBUI_AUDIO_STT_MODEL:-stt-whisper-large-v3-turbo}"
OPENWEBUI_AUDIO_TTS_MODEL="${OPENWEBUI_AUDIO_TTS_MODEL:-tts-qwen3-1.7b}"
LOCALAI_VAD_MODEL="${LOCALAI_VAD_MODEL:-vad-silero}"
SEARXNG_HOST_PORT="${SEARXNG_HOST_PORT:-8081}"
JUPYTER_HOST="${JUPYTER_HOST:-127.0.0.1}"
JUPYTER_PORT="${JUPYTER_PORT:-8888}"
SHELLBOX_HOST_PORT="${SHELLBOX_HOST_PORT:-8001}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

json_tool() { command -v jq >/dev/null && jq -C . || cat; }

json_get_field() {
    local field="$1"
    python3 -c '
import json
import sys

field = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

value = data
for part in field.split("."):
    if isinstance(value, dict):
        value = value.get(part, "")
    else:
        value = ""
        break

if isinstance(value, str):
    print(value)
elif value is None:
    print("")
else:
    print(json.dumps(value))
' "$field"
}

cleanup() {
    [ -n "${LOCALAI_TTS_TMP:-}" ] && rm -f "$LOCALAI_TTS_TMP"
}
trap cleanup EXIT
SMOKE_FAILED=0

if [ "$LLM_BACKEND" = "llama" ]; then
    echo "== 1. llama-server: /v1/models =="
    curl -fsS "http://localhost:$LLAMA_HOST_PORT/v1/models" | json_tool \
        || die "llama-server /v1/models недоступен"
    ok "llama-server отвечает"

    echo
    echo "== 2. llama-server: /v1/chat/completions (2+2?) =="
    curl -fsS "http://localhost:$LLAMA_HOST_PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$PUBLIC_MODEL_ALIAS\",\"messages\":[{\"role\":\"user\",\"content\":\"2+2?\"}],\"max_tokens\":32}" \
        | json_tool || die "llama-server chat.completions упал"
    ok "llama-server прямой чат OK"
elif [ "$LLM_BACKEND" = "llamacpp" ]; then
    echo "== 1. host-side llama.cpp: /v1/models =="
    curl -fsS "http://localhost:$LMSTUDIO_HOST_PORT/v1/models" | json_tool \
        || die "llama.cpp /v1/models недоступен"
    ok "llama.cpp API отвечает"

    echo
    echo "== 2. host-side llama.cpp: /v1/chat/completions (2+2?) =="
    curl -fsS "http://localhost:$LMSTUDIO_HOST_PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$PUBLIC_MODEL_ALIAS\",\"messages\":[{\"role\":\"user\",\"content\":\"2+2?\"}],\"max_tokens\":32}" \
        | json_tool || die "llama.cpp chat.completions упал"
    ok "llama.cpp прямой чат OK"
else
    echo "== 1. LM Studio: /v1/models =="
    curl -fsS "http://localhost:$LMSTUDIO_HOST_PORT/v1/models" | json_tool \
        || die "LM Studio /v1/models недоступен"
    ok "LM Studio API отвечает"

    echo
    echo "== 2. LM Studio: /v1/responses (JIT load при необходимости) =="
    curl -fsS "http://localhost:$LMSTUDIO_HOST_PORT/v1/responses" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$LMSTUDIO_MODEL_ID\",\"input\":\"Ответь одним числом: 2+2?\",\"max_output_tokens\":32}" \
        | json_tool || die "LM Studio responses упал"
    ok "LM Studio прямой вызов OK"
fi

echo
echo "== 3. LiteLLM: /v1/models (auth) =="
curl -fsS "http://localhost:$LITELLM_HOST_PORT/v1/models" \
    -H "Authorization: Bearer $LITELLM_API_KEY" \
    | json_tool || die "LiteLLM /v1/models недоступен"
ok "LiteLLM /v1/models отвечает"

echo
echo "== 4. LiteLLM: /v1/chat/completions (через прокси, должен породить Phoenix span) =="
curl -fsS "http://localhost:$LITELLM_HOST_PORT/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$PUBLIC_MODEL_ALIAS\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":32}" \
    | json_tool || die "LiteLLM chat.completions упал"
if [ "$LLM_BACKEND" = "llama" ]; then
    ok "LiteLLM проксирует → llama-server"
elif [ "$LLM_BACKEND" = "llamacpp" ]; then
    ok "LiteLLM проксирует → host-side llama.cpp"
else
    ok "LiteLLM проксирует → LM Studio"
fi

echo
echo "== 4b. OpenHands sandbox path: bridge → host.docker.internal → LiteLLM =="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    OH_BRIDGE_MODELS="$(
        docker run --rm \
            --add-host=host.docker.internal:host-gateway \
            curlimages/curl:8.11.1 \
            -fsS -m 25 \
            -H "Authorization: Bearer $LITELLM_API_KEY" \
            "http://host.docker.internal:$LITELLM_HOST_PORT/v1/models" 2>/dev/null || true
    )"
    if [ -n "$OH_BRIDGE_MODELS" ] && printf '%s' "$OH_BRIDGE_MODELS" | grep -q '"data"'; then
        ok "Ephemeral bridge-контейнер видит LiteLLM /v1/models через host.docker.internal:$LITELLM_HOST_PORT (как runtime sandbox OpenHands)"
        if printf '%s' "$OH_BRIDGE_MODELS" | grep -q 'stack-openai-relay-qwen36'; then
            ok "В каталоге есть stack-openai-relay-qwen36 (pattern-B relay поднят)"
        else
            warn "Алиас stack-openai-relay-qwen36 не в /v1/models — выполните stack-start / docker compose для compose.phoenix.yml (openai-stack-relay)"
        fi
    else
        warn "Smoke OpenHands path: ответ /v1/models пуст или Docker недоступен (нужен работающий LiteLLM на localhost:$LITELLM_HOST_PORT)"
    fi
else
    warn "Docker недоступен — пропуск проверки host.docker.internal из bridge-сети"
fi

echo
echo "== 5. Phoenix UI и LiteLLM traces =="
code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PHOENIX_HOST_PORT")
if [ "$code" = "200" ]; then
    ok "Phoenix UI :$PHOENIX_HOST_PORT → HTTP 200"
else
    warn "Phoenix UI вернул HTTP $code — проверьте docker logs phoenix"
fi

PHOENIX_SPANS_URL="http://localhost:$PHOENIX_HOST_PORT/v1/projects/$PHOENIX_PROJECT_NAME/spans?span_kind=LLM&limit=5"
PHOENIX_SPANS_JSON=""
PHOENIX_LLM_SPAN_COUNT=0
for _ in {1..10}; do
    PHOENIX_SPANS_JSON="$(curl -fsS "$PHOENIX_SPANS_URL" 2>/dev/null || true)"
    if [ -n "$PHOENIX_SPANS_JSON" ]; then
        PHOENIX_LLM_SPAN_COUNT="$(
            printf '%s' "$PHOENIX_SPANS_JSON" \
                | python3 -c 'import json, sys; print(len((json.load(sys.stdin).get("data") or [])))' 2>/dev/null \
                || echo 0
        )"
        if [ "${PHOENIX_LLM_SPAN_COUNT:-0}" -gt 0 ] 2>/dev/null; then
            break
        fi
    fi
    sleep 2
done

if [ -n "$PHOENIX_SPANS_JSON" ]; then
    if [ "${PHOENIX_LLM_SPAN_COUNT:-0}" -gt 0 ] 2>/dev/null; then
        ok "Phoenix REST API видит ${PHOENIX_LLM_SPAN_COUNT} LLM span(ов) в проекте $PHOENIX_PROJECT_NAME"
    else
        die "Phoenix REST API доступен, но LLM spans для проекта $PHOENIX_PROJECT_NAME не найдены"
    fi
else
    warn "Phoenix REST API $PHOENIX_SPANS_URL недоступен — проверьте трейсы вручную в UI"
fi

echo
echo "== 6. LocalAI audio/VAD: реестр моделей =="
LOCALAI_MODELS_JSON="$(curl -fsS -m 10 "http://localhost:$LOCALAI_HOST_PORT/v1/models" 2>/dev/null || true)"
LOCALAI_HAS_STT=0
LOCALAI_HAS_TTS=0
LOCALAI_HAS_VAD=0
if [ -n "$LOCALAI_MODELS_JSON" ]; then
    printf '%s\n' "$LOCALAI_MODELS_JSON" | json_tool
    ok "LocalAI /v1/models OK"
    if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
        if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q 'qwen3.6-35b-heretic'; then
            ok "qwen3.6-35b-heretic присутствует в LocalAI (legacy-профиль включён)"
        else
            warn "Legacy-профиль включён, но qwen3.6-35b-heretic не найден в LocalAI"
        fi
    else
        if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q 'qwen3.6-35b-heretic'; then
            warn "qwen3.6-35b-heretic всё ещё в LocalAI — перезапустите stack-start.sh или удалите вручную"
        else
            ok "qwen3.6-35b-heretic отсутствует в реестре LocalAI (как и задумано)"
        fi
    fi

    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_STT_MODEL\""; then
        LOCALAI_HAS_STT=1
        ok "Найден STT alias $OPENWEBUI_AUDIO_STT_MODEL"
    else
        warn "Не найден STT alias $OPENWEBUI_AUDIO_STT_MODEL"
    fi

    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_TTS_MODEL\""; then
        LOCALAI_HAS_TTS=1
        ok "Найден TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
    else
        warn "Не найден TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
    fi

    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$LOCALAI_VAD_MODEL\""; then
        LOCALAI_HAS_VAD=1
        ok "Найден VAD alias $LOCALAI_VAD_MODEL"
    else
        warn "Не найден VAD alias $LOCALAI_VAD_MODEL"
    fi
else
    warn "LocalAI недоступен — STT/TTS/VAD проверки пропущены"
fi

if [ "$LOCALAI_HAS_TTS" = "1" ]; then
    echo
    echo "== 7. LiteLLM -> LocalAI TTS: /v1/audio/speech =="
    LOCALAI_TTS_TMP="$(mktemp /tmp/localai-tts.XXXXXX.wav)"
    tts_code=$(curl -sS -o "$LOCALAI_TTS_TMP" -w "%{http_code}" \
        "http://localhost:$LITELLM_HOST_PORT/v1/audio/speech" \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$OPENWEBUI_AUDIO_TTS_MODEL\",\"input\":\"Привет, это проверка OpenWebUI, LiteLLM и LocalAI.\"}" || echo "000")
    if [ "$tts_code" = "200" ] && [ -s "$LOCALAI_TTS_TMP" ]; then
        ok "LiteLLM проксировал TTS в LocalAI ($(wc -c < "$LOCALAI_TTS_TMP") bytes)"
    else
        warn "LiteLLM TTS недоступен (HTTP $tts_code)"
        warn "Используйте browser TTS fallback в OpenWebUI: User Settings → Audio → Web API / Browser Kokoro"
        rm -f "$LOCALAI_TTS_TMP"
        unset LOCALAI_TTS_TMP
    fi
fi

if [ "$LOCALAI_HAS_STT" = "1" ] && [ -n "${LOCALAI_TTS_TMP:-}" ] && [ -s "$LOCALAI_TTS_TMP" ]; then
    echo
    echo "== 8. LiteLLM -> LocalAI STT: /v1/audio/transcriptions =="
    STT_JSON="$(curl -fsS "http://localhost:$LITELLM_HOST_PORT/v1/audio/transcriptions" \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        -F "file=@$LOCALAI_TTS_TMP" \
        -F "model=$OPENWEBUI_AUDIO_STT_MODEL" \
        -F "language=ru")" || STT_JSON=""
    if [ -n "$STT_JSON" ]; then
        printf '%s\n' "$STT_JSON" | json_tool
        STT_TEXT="$(printf '%s' "$STT_JSON" | json_get_field text 2>/dev/null || true)"
        if [ -n "$STT_TEXT" ]; then
            ok "LiteLLM проксировал STT в LocalAI"
        else
            warn "LiteLLM STT ответил, но поле text пустое"
        fi
    else
        warn "LiteLLM STT проверка не прошла"
        warn "Используйте browser STT fallback в OpenWebUI: User Settings → Audio → STT Engine → Web API"
    fi
fi

if [ "$OPENWEBUI_ENABLED" = "1" ]; then
    echo
    echo "== 9. OpenWebUI: /health =="
    if curl -fsS "http://localhost:$OPENWEBUI_HOST_PORT/health" >/dev/null; then
        ok "OpenWebUI /health отвечает"

        if [ -n "$OPENWEBUI_VALIDATE_EMAIL" ] && [ -n "$OPENWEBUI_VALIDATE_PASSWORD" ]; then
            echo
            echo "== 10. OpenWebUI: /api/models (auth) =="
            OPENWEBUI_TOKEN="$(
                curl -fsS -X POST "http://localhost:$OPENWEBUI_HOST_PORT/api/v1/auths/signin" \
                    -H "Content-Type: application/json" \
                    -d "{\"email\":\"$OPENWEBUI_VALIDATE_EMAIL\",\"password\":\"$OPENWEBUI_VALIDATE_PASSWORD\"}" \
                    | json_get_field token 2>/dev/null || true
            )"
            [ -n "$OPENWEBUI_TOKEN" ] || die "OpenWebUI signin не вернул JWT token"

            OPENWEBUI_MODELS_JSON="$(
                curl -fsS "http://localhost:$OPENWEBUI_HOST_PORT/api/models" \
                    -H "Authorization: Bearer $OPENWEBUI_TOKEN"
            )"
            printf '%s\n' "$OPENWEBUI_MODELS_JSON" | json_tool
            if printf '%s' "$OPENWEBUI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_VALIDATE_MODEL\""; then
                ok "OpenWebUI видит модель $OPENWEBUI_VALIDATE_MODEL через LiteLLM"
            else
                warn "OpenWebUI не показал модель $OPENWEBUI_VALIDATE_MODEL в /api/models"
            fi

            echo
            echo "== 11. OpenWebUI: /api/chat/completions =="
            curl -fsS -X POST "http://localhost:$OPENWEBUI_HOST_PORT/api/chat/completions" \
                -H "Authorization: Bearer $OPENWEBUI_TOKEN" \
                -H "Content-Type: application/json" \
                -d "{\"model\":\"$OPENWEBUI_VALIDATE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Ответь словом READY\"}],\"temperature\":0}" \
                | json_tool || die "OpenWebUI chat.completions упал"
            ok "OpenWebUI чат проходит через LiteLLM"
        else
            warn "OpenWebUI deep-check пропущен: задайте OPENWEBUI_VALIDATE_EMAIL/PASSWORD"
            warn "или OPENWEBUI_ADMIN_EMAIL/PASSWORD для headless bootstrap и auth-проверок"
        fi
    else
        warn "OpenWebUI /health недоступен"
        SMOKE_FAILED=1
    fi
fi

echo
echo "== A. SearXNG (web search backend) =="
if curl -fsS -m 5 "http://localhost:$SEARXNG_HOST_PORT/" >/dev/null 2>&1; then
    SEARXNG_RESULTS_JSON="$(curl -fsS -m 10 "http://localhost:$SEARXNG_HOST_PORT/search?q=hello&format=json" 2>/dev/null || true)"
    if [ -n "$SEARXNG_RESULTS_JSON" ] \
        && printf '%s' "$SEARXNG_RESULTS_JSON" \
        | python3 -c 'import json,sys; sys.exit(0 if (json.load(sys.stdin).get("results") or []) else 1)' \
        2>/dev/null; then
        ok "SearXNG /search?format=json возвращает результаты на :$SEARXNG_HOST_PORT"
    else
        warn "SearXNG отвечает, но JSON-search вернул пусто (rate limit или engines)."
    fi
else
    warn "SearXNG недоступен на :$SEARXNG_HOST_PORT (compose.searxng.yml не запущен?)"
fi

echo
echo "== B. host-side Jupyter (Code Interpreter) =="
if [ -n "${JUPYTER_TOKEN:-}" ]; then
    JUPYTER_API_JSON="$(curl -fsS -m 5 "http://${JUPYTER_HOST}:${JUPYTER_PORT}/api?token=$JUPYTER_TOKEN" 2>/dev/null || true)"
    if [ -n "$JUPYTER_API_JSON" ] && printf '%s' "$JUPYTER_API_JSON" | grep -q '"version"'; then
        ok "jupyter_server /api отвечает (token OK)"
        if curl -fsS -m 5 "http://${JUPYTER_HOST}:${JUPYTER_PORT}/api/kernelspecs?token=$JUPYTER_TOKEN" \
            | python3 -c 'import json,sys; ks=json.load(sys.stdin).get("kernelspecs",{}); sys.exit(0 if "venv-jupyter" in ks or "python3" in ks else 1)' \
            2>/dev/null; then
            ok "kernelspec доступен (python3/venv-jupyter)"
        else
            warn "kernelspec не виден — установите ipykernel в .venv-jupyter"
        fi
    else
        warn "jupyter_server /api не ответил — Code Interpreter в OpenWebUI не сработает"
    fi
else
    warn "JUPYTER_TOKEN не задан — пропускаю jupyter smoke"
fi

echo
echo "== C. shellbox (mcpo + mcp-shell-server) =="
if [ -n "${SHELLBOX_API_KEY:-}" ]; then
    SHELLBOX_OPENAPI_JSON="$(
        curl -fsS -m 5 \
            -H "Authorization: Bearer $SHELLBOX_API_KEY" \
            "http://127.0.0.1:$SHELLBOX_HOST_PORT/openapi.json" 2>/dev/null || true
    )"
    if [ -n "$SHELLBOX_OPENAPI_JSON" ]; then
        TOOL_PATHS="$(printf '%s' "$SHELLBOX_OPENAPI_JSON" \
            | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(sorted((d.get("paths") or {}).keys())))' 2>/dev/null || true)"
        if [ -n "$TOOL_PATHS" ]; then
            ok "shellbox /openapi.json доступен; routes: $TOOL_PATHS"
        else
            warn "shellbox /openapi.json не содержит paths — проверьте mcpo logs"
        fi
    else
        warn "shellbox /openapi.json не отвечает (compose.shellbox.yml не запущен?)"
    fi
else
    warn "SHELLBOX_API_KEY не задан — пропускаю shellbox smoke"
fi

echo
echo "== D. agent-mesh adapters (clawcode-adapter + openhands-adapter + opencode-adapter) =="
CLAWCODE_ADAPTER_HOST_PORT="${CLAWCODE_ADAPTER_HOST_PORT:-8790}"
OPENHANDS_ADAPTER_HOST_PORT="${OPENHANDS_ADAPTER_HOST_PORT:-8791}"
OPENCODE_ADAPTER_HOST_PORT="${OPENCODE_ADAPTER_HOST_PORT:-8798}"
MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}"
for adapter in \
        "clawcode-adapter:${CLAWCODE_ADAPTER_HOST_PORT}:${CLAWCODE_ADAPTER_API_KEY:-}" \
        "openhands-adapter:${OPENHANDS_ADAPTER_HOST_PORT}:${OPENHANDS_ADAPTER_API_KEY:-}" \
        "opencode-adapter:${OPENCODE_ADAPTER_HOST_PORT}:${OPENCODE_ADAPTER_API_KEY:-}"; do
    name="${adapter%%:*}"
    rest="${adapter#*:}"
    port="${rest%%:*}"
    key="${rest#*:}"
    if [ -z "$key" ]; then
        warn "$name пропущен: соответствующий API_KEY не задан в .env"
        continue
    fi
    if ! curl -fsS -m 5 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
        warn "$name /healthz не отвечает (compose.agents-mesh.yml не запущен?)"
        continue
    fi
    ok "$name /healthz отвечает на :$port"
    OPENAPI_STATUS="$(curl -s -o /dev/null -m 5 -w "%{http_code}" \
        -H "Authorization: Bearer $key" \
        "http://127.0.0.1:$port/openapi.json")"
    if [ "$OPENAPI_STATUS" = "200" ]; then
        ok "$name /openapi.json доступен с bearer-токеном"
    else
        warn "$name /openapi.json вернул HTTP $OPENAPI_STATUS"
    fi
    # Cycle guard probe: simulate already-at-limit depth → expect 429.
    GUARD_STATUS="$(curl -s -o /dev/null -m 5 -w "%{http_code}" \
        -X POST "http://127.0.0.1:$port/v1/run" \
        -H "Authorization: Bearer $key" \
        -H "Content-Type: application/json" \
        -H "X-Agent-Mesh-Depth: $MAX_NESTED_AGENT_CALLS" \
        -d '{"task":"cycle-guard probe","timeout_s":10}')"
    if [ "$GUARD_STATUS" = "429" ]; then
        ok "$name cycle-guard сработал (X-Agent-Mesh-Depth=$MAX_NESTED_AGENT_CALLS → HTTP 429)"
    else
        warn "$name cycle-guard вернул HTTP $GUARD_STATUS (ожидалось 429 при depth=$MAX_NESTED_AGENT_CALLS)"
    fi
done

# opencode-specific: ACP-bridge requires идущий контейнер opencode.
if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    if docker inspect --format='{{.State.Status}}' opencode 2>/dev/null | grep -q running; then
        ok "контейнер opencode запущен (ACP-bridge готов)"
    else
        warn "контейнер opencode не запущен — opencode-adapter /v1/run будет падать. Поднять: bash opencode-start.sh --no-attach"
    fi
fi

echo
echo "== 12. Coexistence: проверка занятости стандартных портов =="
# OpenHands :3300, opencode Web UI :3400 — наши; их default'ы не должны
# совпадать с OpenWebUI :3000.
if [ "$OPENWEBUI_HOST_PORT" = "3300" ] || [ "$OPENWEBUI_HOST_PORT" = "3400" ]; then
    die "OPENWEBUI_HOST_PORT=$OPENWEBUI_HOST_PORT конфликтует с OpenHands (:3300) или opencode Web UI (:3400)"
fi
ok "OpenWebUI host port не конфликтует с OpenHands (:3300) и opencode (:3400)"

echo
if [ "$SMOKE_FAILED" -ne 0 ]; then
    die "Есть незакрытые smoke-check ошибки; см. предупреждения выше."
fi

echo
echo "Все базовые проверки завершены."
if [ "$LOCALAI_HAS_VAD" = "1" ]; then
    echo "VAD alias $LOCALAI_VAD_MODEL доступен напрямую в LocalAI (/v1/vad); LiteLLM его не проксирует."
fi
echo "Если LocalAI STT/TTS не готовы, OpenWebUI всё равно пригоден для чата через LiteLLM."
echo "Fallback для аудио: User Settings → Audio → STT Engine = Web API; TTS Engine = Web API или Browser Kokoro."
