#!/usr/bin/env bash
# Быстрый русскоязычный demo-path для OpenWebUI + LiteLLM + LocalAI + Phoenix.
# Предполагается, что стек уже поднят через stack-start.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
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
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    PHOENIX_HOST_PORT \
    LOCALAI_HOST_PORT \
    PUBLIC_MODEL_ALIAS

load_selected_env "$OPENWEBUI_ENV_FILE" \
    OPENWEBUI_HOST_PORT \
    OPENWEBUI_DEFAULT_MODEL \
    OPENWEBUI_DEFAULT_LOCALE \
    OPENWEBUI_VALIDATE_EMAIL \
    OPENWEBUI_VALIDATE_PASSWORD \
    OPENWEBUI_ADMIN_EMAIL \
    OPENWEBUI_ADMIN_PASSWORD \
    OPENWEBUI_AUDIO_STT_MODEL \
    OPENWEBUI_AUDIO_TTS_MODEL \
    OPENWEBUI_AUDIO_TTS_VOICE

LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
PHOENIX_HOST_PORT="${PHOENIX_HOST_PORT:-6006}"
LOCALAI_HOST_PORT="${LOCALAI_HOST_PORT:-8180}"
PUBLIC_MODEL_ALIAS="${PUBLIC_MODEL_ALIAS:-qwen3.6-35b-heretic}"
OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
OPENWEBUI_DEFAULT_MODEL="${OPENWEBUI_DEFAULT_MODEL:-$PUBLIC_MODEL_ALIAS}"
OPENWEBUI_DEFAULT_LOCALE="${OPENWEBUI_DEFAULT_LOCALE:-ru-RU}"
OPENWEBUI_VALIDATE_EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
OPENWEBUI_VALIDATE_PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"
OPENWEBUI_AUDIO_STT_MODEL="${OPENWEBUI_AUDIO_STT_MODEL:-stt-whisper-large-v3-turbo}"
OPENWEBUI_AUDIO_TTS_MODEL="${OPENWEBUI_AUDIO_TTS_MODEL:-tts-qwen3-1.7b}"
OPENWEBUI_AUDIO_TTS_VOICE="${OPENWEBUI_AUDIO_TTS_VOICE:-Aiden}"
LOCALAI_VAD_MODEL="${LOCALAI_VAD_MODEL:-vad-silero}"

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

extract_openwebui_text() {
    python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

choices = data.get("choices") or []
message = (choices[0].get("message") or {}) if choices else {}
content = message.get("content", "")
if isinstance(content, list):
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    content = "\n".join(part for part in parts if part)
print(content if isinstance(content, str) else "")
'
}

cleanup() {
    [ -n "${LOCALAI_TTS_TMP:-}" ] && rm -f "$LOCALAI_TTS_TMP"
}
trap cleanup EXIT

echo "== 1. Readiness =="
curl -fsS "http://localhost:$OPENWEBUI_HOST_PORT/health" >/dev/null \
    || die "OpenWebUI /health недоступен на :$OPENWEBUI_HOST_PORT"
ok "OpenWebUI /health отвечает"

LITELLM_MODELS_JSON="$(
    curl -fsS "http://localhost:$LITELLM_HOST_PORT/v1/models" \
        -H "Authorization: Bearer $LITELLM_API_KEY"
)"
if printf '%s' "$LITELLM_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_DEFAULT_MODEL\""; then
    ok "LiteLLM публикует модель $OPENWEBUI_DEFAULT_MODEL"
else
    warn "LiteLLM не показал модель $OPENWEBUI_DEFAULT_MODEL"
fi

LOCALAI_MODELS_JSON="$(curl -fsS -m 10 "http://localhost:$LOCALAI_HOST_PORT/v1/models" 2>/dev/null || true)"
LOCALAI_HAS_STT=0
LOCALAI_HAS_TTS=0
LOCALAI_HAS_VAD=0
if [ -n "$LOCALAI_MODELS_JSON" ]; then
    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_STT_MODEL\""; then
        LOCALAI_HAS_STT=1
        ok "LocalAI публикует STT alias $OPENWEBUI_AUDIO_STT_MODEL"
    else
        warn "LocalAI не показал STT alias $OPENWEBUI_AUDIO_STT_MODEL"
    fi
    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_TTS_MODEL\""; then
        LOCALAI_HAS_TTS=1
        ok "LocalAI публикует TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
    else
        warn "LocalAI не показал TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
    fi

    if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$LOCALAI_VAD_MODEL\""; then
        LOCALAI_HAS_VAD=1
        ok "LocalAI публикует VAD alias $LOCALAI_VAD_MODEL"
    else
        warn "LocalAI не показал VAD alias $LOCALAI_VAD_MODEL"
    fi
else
    warn "LocalAI /v1/models недоступен; аудио-часть демо будет только ручной"
fi

echo
echo "== 2. Live demo checklist =="
echo " OpenWebUI URL:    http://localhost:$OPENWEBUI_HOST_PORT"
echo " Интерфейс:        $OPENWEBUI_DEFAULT_LOCALE по умолчанию"
echo " Модель:           $OPENWEBUI_DEFAULT_MODEL"
echo " STT через LiteLLM: $OPENWEBUI_AUDIO_STT_MODEL"
echo " TTS через LiteLLM: $OPENWEBUI_AUDIO_TTS_MODEL"
echo " Голос TTS:        $OPENWEBUI_AUDIO_TTS_VOICE"
echo " Phoenix traces:   http://localhost:$PHOENIX_HOST_PORT"
warn "Если UI уже кешировал английский язык, откройте приватное окно или переключите Settings -> General -> WebUI Settings -> Language -> Russian."

if [ -n "$OPENWEBUI_VALIDATE_EMAIL" ] && [ -n "$OPENWEBUI_VALIDATE_PASSWORD" ]; then
    echo
    echo "== 3. Headless OpenWebUI demo (auth) =="
    OPENWEBUI_TOKEN="$(
        curl -fsS -X POST "http://localhost:$OPENWEBUI_HOST_PORT/api/v1/auths/signin" \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"$OPENWEBUI_VALIDATE_EMAIL\",\"password\":\"$OPENWEBUI_VALIDATE_PASSWORD\"}" \
            | json_get_field token 2>/dev/null || true
    )"
    [ -n "$OPENWEBUI_TOKEN" ] || die "OpenWebUI signin не вернул JWT token"
    ok "OpenWebUI auth успешен для $OPENWEBUI_VALIDATE_EMAIL"

    OPENWEBUI_MODELS_JSON="$(
        curl -fsS "http://localhost:$OPENWEBUI_HOST_PORT/api/models" \
            -H "Authorization: Bearer $OPENWEBUI_TOKEN"
    )"
    if printf '%s' "$OPENWEBUI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_DEFAULT_MODEL\""; then
        ok "OpenWebUI /api/models показывает $OPENWEBUI_DEFAULT_MODEL"
    else
        warn "OpenWebUI /api/models не показал $OPENWEBUI_DEFAULT_MODEL"
    fi

    DEMO_PROMPT="Коротко представься на русском в одном предложении и закончи словом ГОТОВО."
    OPENWEBUI_CHAT_JSON="$(
        curl -fsS -X POST "http://localhost:$OPENWEBUI_HOST_PORT/api/chat/completions" \
            -H "Authorization: Bearer $OPENWEBUI_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"$OPENWEBUI_DEFAULT_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$DEMO_PROMPT\"}],\"temperature\":0.2}"
    )"
    printf '%s\n' "$OPENWEBUI_CHAT_JSON" | json_tool

    DEMO_TEXT="$(printf '%s' "$OPENWEBUI_CHAT_JSON" | extract_openwebui_text 2>/dev/null || true)"
    if [ -n "$DEMO_TEXT" ]; then
        ok "OpenWebUI вернул русскоязычный ответ"
    else
        warn "OpenWebUI ответил, но текст ответа не удалось извлечь"
    fi
else
    warn "Headless OpenWebUI demo пропущен: задайте OPENWEBUI_VALIDATE_EMAIL/PASSWORD или OPENWEBUI_ADMIN_EMAIL/PASSWORD."
fi

if [ "$LOCALAI_HAS_TTS" = "1" ]; then
    echo
    echo "== 4. Russian audio demo via LiteLLM -> LocalAI =="
    LOCALAI_TTS_TMP="$(mktemp /tmp/openwebui-demo-ru.XXXXXX.wav)"
    TTS_TEXT="Здравствуйте! Это демо OpenWebUI на русском языке."
    TTS_CODE="$(
        curl -sS -o "$LOCALAI_TTS_TMP" -w "%{http_code}" \
            "http://localhost:$LITELLM_HOST_PORT/v1/audio/speech" \
            -H "Authorization: Bearer $LITELLM_API_KEY" \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"$OPENWEBUI_AUDIO_TTS_MODEL\",\"voice\":\"$OPENWEBUI_AUDIO_TTS_VOICE\",\"input\":\"$TTS_TEXT\"}" \
            || echo "000"
    )"
    if [ "$TTS_CODE" = "200" ] && [ -s "$LOCALAI_TTS_TMP" ]; then
        ok "LiteLLM проксировал русское TTS-аудио ($(wc -c < "$LOCALAI_TTS_TMP") bytes)"
    else
        warn "LiteLLM TTS недоступен (HTTP $TTS_CODE)"
        rm -f "$LOCALAI_TTS_TMP"
        unset LOCALAI_TTS_TMP
    fi

    if [ "$LOCALAI_HAS_STT" = "1" ] && [ -n "${LOCALAI_TTS_TMP:-}" ] && [ -s "$LOCALAI_TTS_TMP" ]; then
        STT_JSON="$(
            curl -fsS "http://localhost:$LITELLM_HOST_PORT/v1/audio/transcriptions" \
                -H "Authorization: Bearer $LITELLM_API_KEY" \
                -F "file=@$LOCALAI_TTS_TMP" \
                -F "model=$OPENWEBUI_AUDIO_STT_MODEL" \
                -F "language=ru" \
                || true
        )"
        if [ -n "$STT_JSON" ]; then
            printf '%s\n' "$STT_JSON" | json_tool
            STT_TEXT="$(printf '%s' "$STT_JSON" | json_get_field text 2>/dev/null || true)"
            if [ -n "$STT_TEXT" ]; then
                ok "LiteLLM проксировал русскую STT-транскрипцию"
            else
                warn "LiteLLM STT ответил, но поле text пустое"
            fi
        else
            warn "LiteLLM STT round-trip не прошёл"
        fi
    fi
fi

echo
echo "== 5. Что показать в браузере =="
echo " 1. Создайте или откройте чат в OpenWebUI."
echo " 2. Убедитесь, что интерфейс на русском и выбрана модель $OPENWEBUI_DEFAULT_MODEL."
echo " 3. Отправьте один из prompts:"
echo "    - Кратко объясни, как запрос проходит через OpenWebUI, LiteLLM и Phoenix."
echo "    - Составь три идеи голосового ассистента для офиса."
echo "    - Ответь только по-русски и заверши фразой: демонстрация готова."
echo " 4. Нажмите TTS для ответа или запишите короткую русскую реплику через микрофон."
echo " 5. Откройте Phoenix и покажите, что после запроса появился новый LLM span."
if [ "$LOCALAI_HAS_VAD" = "1" ]; then
    echo " 6. При необходимости покажите, что VAD доступен напрямую в LocalAI как $LOCALAI_VAD_MODEL (/v1/vad)."
fi

