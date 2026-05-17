#!/usr/bin/env bash
# Русскоязычный demo-path для OpenHands + LiteLLM (стек уже поднят: stack-start.sh;
# OpenHands — по желанию: openhands-start.sh или флаг --start).
#
# Запуск из корня репо:
#   bash ./openhands-demo-ru.sh
#   bash ./openhands-demo-ru.sh --start   # если UI ещё не поднят — вызовет openhands-start.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OPENHANDS="$SCRIPT_DIR/.env.openhands"

AUTO_START=0
for arg in "$@"; do
    case "$arg" in
        --start) AUTO_START=1 ;;
        -h|--help)
            echo "Usage: bash ./openhands-demo-ru.sh [--start]"
            echo "  --start  если OpenHands UI недоступен — запустить openhands-start.sh"
            exit 0
            ;;
    esac
done

_OH_KEYS=(
    OPENHANDS_HOST_PORT
    OPENHANDS_DEFAULT_MODEL
    OPENHANDS_WORKSPACE_DIR
    OPENHANDS_LITELLM_BASE_URL
    OPENHANDS_LITELLM_API_KEY
    LITELLM_HOST_PORT
    LITELLM_API_KEY
)

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

load_selected_env "$ENV_FILE" "${_OH_KEYS[@]}"
load_selected_env "$ENV_OPENHANDS" "${_OH_KEYS[@]}"

OPENHANDS_HOST_PORT="${OPENHANDS_HOST_PORT:-3300}"
OPENHANDS_DEFAULT_MODEL="${OPENHANDS_DEFAULT_MODEL:-qwen3.6-35b-heretic}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
OPENHANDS_LITELLM_API_KEY="${OPENHANDS_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export OPENHANDS_LITELLM_API_KEY

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

json_tool() { command -v jq >/dev/null && jq -C . || cat; }

extract_chat_content() {
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

echo "== OpenHands — демонстрация (readiness) =="

if ! docker info >/dev/null 2>&1; then
    die "Docker недоступен. Добавьте пользователя в группу docker и перелогиньтесь, затем повторите."
fi
ok "Docker daemon отвечает"

if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    die "Сеть llm-stack-net не найдена. Сначала: bash \"$SCRIPT_DIR/stack-start.sh\""
fi
ok "Сеть llm-stack-net существует"

LITELLM_MODELS_URL="http://localhost:$LITELLM_HOST_PORT/v1/models"
LITELLM_MODELS_JSON="$(
    curl -fsS -m 10 "$LITELLM_MODELS_URL" \
        -H "Authorization: Bearer $LITELLM_API_KEY" 2>/dev/null || true
)"
[ -n "$LITELLM_MODELS_JSON" ] || die "LiteLLM $LITELLM_MODELS_URL не отвечает. Запустите: bash \"$SCRIPT_DIR/stack-start.sh\""
ok "LiteLLM /v1/models отвечает"

if printf '%s' "$LITELLM_MODELS_JSON" | grep -q "\"id\":\"$OPENHANDS_DEFAULT_MODEL\""; then
    ok "LiteLLM публикует модель $OPENHANDS_DEFAULT_MODEL (как в OpenHands: litellm_proxy/$OPENHANDS_DEFAULT_MODEL)"
else
    warn "В /v1/models нет алиаса $OPENHANDS_DEFAULT_MODEL — проверьте docker/litellm/config.yaml или смените OPENHANDS_DEFAULT_MODEL"
fi

oh_ui_up() {
    curl -fsS -m 3 "http://localhost:$OPENHANDS_HOST_PORT/" >/dev/null 2>&1
}

if ! oh_ui_up; then
    if [ "$AUTO_START" = "1" ]; then
        warn "OpenHands UI недоступен — запускаю openhands-start.sh …"
        bash "$SCRIPT_DIR/openhands-start.sh"
    else
        die "OpenHands UI недоступен на :$OPENHANDS_HOST_PORT. Запустите: bash \"$SCRIPT_DIR/openhands-start.sh\" или повторите с флагом --start"
    fi
fi
ok "OpenHands UI отвечает на http://localhost:$OPENHANDS_HOST_PORT"

echo
echo "== Headless: тот же LLM-путь, что использует OpenHands (через LiteLLM) =="
DEMO_PROMPT="Ответь одним коротким предложением по-русски: кто ты и через какой шлюз идёт запрос. Закончи словом ГОТОВО."
CHAT_JSON="$(
    curl -fsS -m 120 -X POST "http://localhost:$LITELLM_HOST_PORT/v1/chat/completions" \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$OPENHANDS_DEFAULT_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$DEMO_PROMPT\"}],\"temperature\":0.3}" \
        || true
)"
if [ -z "$CHAT_JSON" ]; then
    warn "LiteLLM chat/completions не вернул тело (upstream llama.cpp/LM Studio выключен?)"
else
    printf '%s\n' "$CHAT_JSON" | json_tool
    DEMO_TEXT="$(printf '%s' "$CHAT_JSON" | extract_chat_content 2>/dev/null || true)"
    if [ -n "$DEMO_TEXT" ]; then
        ok "LiteLLM вернул ответ для модели $OPENHANDS_DEFAULT_MODEL"
    else
        warn "Ответ есть, но content не извлечён — смотрите JSON выше"
    fi
fi

echo
echo "== Живая демонстрация в браузере (чеклист) =="
echo " OpenHands UI:     http://localhost:$OPENHANDS_HOST_PORT"
echo " OpenWebUI:        см. stack-demo-ru.sh (общий LiteLLM, тот же model_list)"
echo " Модель в OH:      litellm_proxy/$OPENHANDS_DEFAULT_MODEL (уже в env compose)"
echo ""
echo " 1. Откройте OpenHands → Settings → LLM: убедитесь, что Base URL указывает на"
echo "    http://litellm:4000/v1 (внутри контейнера) — это задаётся при старте."
echo " 2. Создайте новую сессию агента и попросите по-русски:"
echo "    - Кратко опиши, какие файлы видишь в /workspace."
echo "    - Создай в /workspace маленький файл demo-ru.txt с одной строкой текста."
echo " 3. При первом запуске инструментов дождитесь pull runtime sandbox-образа (~2–3 ГБ)."
echo " 4. Остановка только OpenHands: bash \"$SCRIPT_DIR/openhands-stop.sh\""

ok "Демонстрация готова (инфраструктура и headless LLM проверены)."
