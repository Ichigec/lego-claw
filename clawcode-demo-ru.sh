#!/usr/bin/env bash
# Headless smoke / demo путь для Claw Code (стек уже поднят: stack-start.sh).
#
# Что проверяет:
#   1. Docker daemon и compose-сеть llm-stack-net.
#   2. LiteLLM /v1/models отвечает и публикует CLAWCODE_DEFAULT_MODEL.
#   3. Контейнер clawcode либо уже работает, либо поднимается через
#      clawcode-start.sh --no-attach (если передан флаг --start).
#   4. Внутри контейнера переменные среды (OPENAI_API_BASE, MCP_SERVERS).
#   5. headless chat-completion через тот же LiteLLM-алиас, который
#      использует Claw Code — гарантирует, что модель отвечает по-русски.
#
# Запуск:
#   bash ./clawcode-demo-ru.sh             # требует, чтобы контейнер уже работал
#   bash ./clawcode-demo-ru.sh --start     # поднять контейнер автоматически
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_CLAWCODE="$SCRIPT_DIR/.env.clawcode"

AUTO_START=0
for arg in "$@"; do
    case "$arg" in
        --start) AUTO_START=1 ;;
        -h|--help)
            echo "Usage: bash $0 [--start]"
            echo "  --start  если контейнер clawcode не работает — запустить clawcode-start.sh --no-attach"
            exit 0
            ;;
    esac
done

_CC_KEYS=(
    CLAWCODE_DEFAULT_MODEL
    CLAWCODE_WORKSPACE_DIR
    CLAWCODE_LITELLM_BASE_URL
    CLAWCODE_LITELLM_API_KEY
    CLAWCODE_MCP_SERVERS
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

load_selected_env "$ENV_FILE" "${_CC_KEYS[@]}"
load_selected_env "$ENV_CLAWCODE" "${_CC_KEYS[@]}"

CLAWCODE_DEFAULT_MODEL="${CLAWCODE_DEFAULT_MODEL:-openai/qwen3.6-35b-heretic}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
CLAWCODE_LITELLM_API_KEY="${CLAWCODE_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export CLAWCODE_LITELLM_API_KEY

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

json_tool() { command -v jq >/dev/null && jq -C . || cat; }

extract_chat_content() {
    python3 -c '
import json, sys
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
    content = "\n".join(p for p in parts if p)
print(content if isinstance(content, str) else "")
'
}

echo "== Claw Code — демонстрация (readiness) =="

if ! docker info >/dev/null 2>&1; then
    die "Docker недоступен. Добавьте пользователя в группу docker и перелогиньтесь."
fi
ok "Docker daemon отвечает"

if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    die "Сеть llm-stack-net не найдена. Сначала: bash \"$SCRIPT_DIR/stack-start.sh\""
fi
ok "Сеть llm-stack-net существует"

LITELLM_MODELS_URL="http://localhost:$LITELLM_HOST_PORT/v1/models"
LITELLM_MODELS_JSON="$(curl -fsS -m 10 "$LITELLM_MODELS_URL" \
    -H "Authorization: Bearer $LITELLM_API_KEY" 2>/dev/null || true)"
[ -n "$LITELLM_MODELS_JSON" ] || die "LiteLLM $LITELLM_MODELS_URL не отвечает. Запустите stack-start.sh."
ok "LiteLLM /v1/models отвечает"

if printf '%s' "$LITELLM_MODELS_JSON" | grep -q "\"id\":\"$CLAWCODE_DEFAULT_MODEL\""; then
    ok "LiteLLM публикует модель $CLAWCODE_DEFAULT_MODEL"
else
    warn "В /v1/models нет $CLAWCODE_DEFAULT_MODEL — Claw Code будет 404, пока вы не выберете другую модель"
fi

cc_running() {
    [ "$(docker inspect --format='{{.State.Status}}' clawcode 2>/dev/null || echo "missing")" = "running" ]
}

if ! cc_running; then
    if [ "$AUTO_START" = "1" ]; then
        warn "Контейнер clawcode не работает — запускаю clawcode-start.sh --no-attach …"
        bash "$SCRIPT_DIR/clawcode-start.sh" --no-attach
    else
        die "Контейнер clawcode не работает. Запустите: bash \"$SCRIPT_DIR/clawcode-start.sh\" или повторите с --start"
    fi
fi
ok "Контейнер clawcode работает (state=running)"

echo
echo "== Внутри контейнера: env-проверки =="
docker exec clawcode env \
    | grep -E '^(OPENAI_API_BASE|OPENAI_API_KEY|LLM_MODEL|MCP_SERVERS|CLAWCODE_HOME)=' \
    | sed -E 's/(OPENAI_API_KEY=).*/\1***redacted***/'

echo
echo "== Внутри контейнера: проверка доступа к LiteLLM =="
docker exec clawcode sh -c '
    set -e
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -m 10 -H "Authorization: Bearer ${OPENAI_API_KEY}" \
            "${OPENAI_API_BASE%/}/models" \
            | python -c "import json,sys; data=json.load(sys.stdin); print(\"models:\", len((data.get(\"data\") or [])))"
    else
        python -c "
import json, os, urllib.request
req = urllib.request.Request(os.environ[\"OPENAI_API_BASE\"].rstrip(\"/\") + \"/models\",
    headers={\"Authorization\": \"Bearer \" + os.environ.get(\"OPENAI_API_KEY\", \"\")})
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())
    print(\"models:\", len(data.get(\"data\") or []))
"
    fi
' || warn "Контейнер не достучался до LiteLLM (см. docker logs clawcode)"

echo
echo "== Headless: тот же LLM-путь, что использует Claw Code =="
DEMO_PROMPT="Ответь одним коротким предложением по-русски: ты модель ${CLAWCODE_DEFAULT_MODEL}, идущая через LiteLLM. Закончи словом ГОТОВО."
CHAT_JSON="$(
    curl -fsS -m 120 -X POST "http://localhost:$LITELLM_HOST_PORT/v1/chat/completions" \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$CLAWCODE_DEFAULT_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$DEMO_PROMPT\"}],\"temperature\":0.3}" \
        || true
)"
if [ -z "$CHAT_JSON" ]; then
    warn "LiteLLM chat/completions не вернул тело (upstream выключен?)"
else
    printf '%s\n' "$CHAT_JSON" | json_tool
    DEMO_TEXT="$(printf '%s' "$CHAT_JSON" | extract_chat_content 2>/dev/null || true)"
    if [ -n "$DEMO_TEXT" ]; then
        ok "LiteLLM вернул ответ для модели $CLAWCODE_DEFAULT_MODEL"
    else
        warn "Ответ есть, но content не извлечён — смотрите JSON выше"
    fi
fi

echo
echo "== Чеклист для интерактивной демонстрации =="
echo " 1. Войдите в REPL:  bash clawcode-start.sh   или вручную:"
echo "      docker exec -it clawcode claw --model openai/qwen3.6-35b-heretic"
echo "    (алиас возьмите из CLAWCODE_DEFAULT_MODEL в .env.clawcode)"
echo " 2. Попросите по-русски:"
echo "     - Кратко опиши файлы в /workspace/project."
echo "     - Создай /workspace/project/clawcode-demo-ru.txt с одной строкой."
echo " 3. Остановка:       bash $SCRIPT_DIR/clawcode-stop.sh"
echo " 4. MCP search:      Claw Code должен видеть tool 'search' через"
echo "    MCP-сервер searchbox (sse-транспорт, llm-stack-net)."
ok "Демонстрация Claw Code готова."
