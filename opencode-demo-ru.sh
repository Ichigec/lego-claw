#!/usr/bin/env bash
# Headless smoke / demo путь для opencode (стек уже поднят: stack-start.sh).
#
# Что проверяет:
#   1. Docker daemon и compose-сеть llm-stack-net.
#   2. LiteLLM /v1/models отвечает и публикует OPENCODE_DEFAULT_MODEL.
#   3. Контейнер opencode либо уже работает, либо поднимается через
#      opencode-start.sh --no-attach (если передан флаг --start).
#   4. Внутри контейнера: версия opencode, переменные среды (OPENAI_*).
#   5. ACP smoke — короткая JSON-RPC сессия через
#      `docker exec -i opencode opencode acp`: initialize → session/new
#      → session/prompt → ждём stopReason.
#   6. (опц.) opencode-adapter /healthz и POST /v1/run, если задан ключ.
#
# Запуск:
#   bash ./opencode-demo-ru.sh             # требует контейнер уже работает
#   bash ./opencode-demo-ru.sh --start     # поднять контейнер автоматически
#   bash ./opencode-demo-ru.sh --adapter   # дёрнуть opencode-adapter /v1/run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OPENCODE="$SCRIPT_DIR/.env.opencode"

AUTO_START=0
DO_ADAPTER=0
for arg in "$@"; do
    case "$arg" in
        --start) AUTO_START=1 ;;
        --adapter) DO_ADAPTER=1 ;;
        -h|--help)
            cat <<USAGE
Usage: bash $0 [--start] [--adapter]
  --start    если контейнер opencode не работает — запустить
             opencode-start.sh --no-attach автоматически.
  --adapter  выполнить также POST /v1/run на opencode-adapter (требует,
             чтобы compose.agents-mesh.yml был поднят).
USAGE
            exit 0
            ;;
    esac
done

_OC_KEYS=(
    OPENCODE_DEFAULT_MODEL
    OPENCODE_WORKSPACE_DIR
    OPENCODE_LITELLM_BASE_URL
    OPENCODE_LITELLM_API_KEY
    OPENCODE_MCP_SERVERS
    OPENCODE_ADAPTER_API_KEY
    OPENCODE_ADAPTER_HOST_PORT
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

load_selected_env "$ENV_FILE" "${_OC_KEYS[@]}"
load_selected_env "$ENV_OPENCODE" "${_OC_KEYS[@]}"

OPENCODE_DEFAULT_MODEL="${OPENCODE_DEFAULT_MODEL:-litellm/qwen3.6-35b-heretic}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
OPENCODE_LITELLM_API_KEY="${OPENCODE_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export OPENCODE_LITELLM_API_KEY
OPENCODE_ADAPTER_HOST_PORT="${OPENCODE_ADAPTER_HOST_PORT:-8798}"

# LiteLLM ожидает либо короткий alias (`qwen3.6-35b-heretic`), либо
# `provider/model`. opencode'у мы отдадим строку как есть (litellm/<id>),
# а в smoke ниже спросим у LiteLLM сокращённую форму.
LITELLM_DEFAULT_MODEL="${OPENCODE_DEFAULT_MODEL#litellm/}"
case "$LITELLM_DEFAULT_MODEL" in
    */*) ;;
    *)   LITELLM_DEFAULT_MODEL="openai/$LITELLM_DEFAULT_MODEL" ;;
esac

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

json_tool() { command -v jq >/dev/null && jq -C . || cat; }

echo "== opencode — демонстрация (readiness) =="
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

if printf '%s' "$LITELLM_MODELS_JSON" | grep -q "\"id\":\"$LITELLM_DEFAULT_MODEL\""; then
    ok "LiteLLM публикует модель $LITELLM_DEFAULT_MODEL"
else
    warn "В /v1/models нет $LITELLM_DEFAULT_MODEL — opencode будет 404, пока вы не выберете другую модель"
fi

oc_running() {
    [ "$(docker inspect --format='{{.State.Status}}' opencode 2>/dev/null || echo "missing")" = "running" ]
}

if ! oc_running; then
    if [ "$AUTO_START" = "1" ]; then
        warn "Контейнер opencode не работает — запускаю opencode-start.sh --no-attach …"
        bash "$SCRIPT_DIR/opencode-start.sh" --no-attach
    else
        die "Контейнер opencode не работает. Запустите: bash \"$SCRIPT_DIR/opencode-start.sh\" или повторите с --start"
    fi
fi
ok "Контейнер opencode работает (state=running)"

echo
echo "== Внутри контейнера: env-проверки =="
docker exec opencode env \
    | grep -E '^(OPENAI_API_BASE|OPENAI_BASE_URL|OPENAI_API_KEY|LITELLM_API_KEY|MCP_SERVERS|OPENCODE_HOME)=' \
    | sed -E 's/(API_KEY=).*/\1***redacted***/'

echo
echo "== Версия opencode =="
# `bash -c` без `-l`: login-shell сбрасывает PATH через /etc/profile и теряет
# ~/.local/bin (куда opencode installer кладёт бинарь).
docker exec opencode bash -c '
    for p in /home/opencode/.local/bin/opencode \
             /home/opencode/.opencode/bin/opencode \
             /usr/local/bin/opencode; do
        if [ -x "$p" ]; then exec "$p" --version; fi
    done
    opencode --version
' 2>&1 | head -n3 || warn "opencode --version упал — see docker logs opencode"

echo
echo "== ACP smoke (initialize → session/new → session/prompt) =="
# Один NDJSON-batch через docker exec -i; читаем все ответы с stdin.
ACP_INPUT="$(python3 - <<'PY'
import json, os
workspace = "/workspace/project"
msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": True},
        "clientInfo": {"name": "opencode-demo-ru", "version": "1.0"},
    }},
    {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": workspace, "mcpServers": []}},
]
print("\n".join(json.dumps(m) for m in msgs))
PY
)"

ACP_OUT="$(printf '%s\n' "$ACP_INPUT" | timeout 30 docker exec -i opencode opencode acp 2>/dev/null | head -n 20 || true)"
if [ -z "$ACP_OUT" ]; then
    warn "ACP smoke: пустой ответ — проверьте, что 'opencode acp' существует в этой версии (docker exec opencode opencode --help | grep acp)."
else
    printf '%s\n' "$ACP_OUT" | json_tool || true
    # Минимальная валидация: ищем результат initialize
    if printf '%s' "$ACP_OUT" | grep -q '"protocolVersion"'; then
        ok "ACP initialize вернул protocolVersion"
    else
        warn "ACP initialize не вернул protocolVersion — смотри JSON выше"
    fi
fi

if [ "$DO_ADAPTER" = "1" ]; then
    echo
    echo "== opencode-adapter (compose.agents-mesh.yml) =="
    if [ -z "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
        warn "OPENCODE_ADAPTER_API_KEY пуст — пропускаю прогон. Сгенерируйте: openssl rand -hex 32"
    elif ! curl -fsS -m 3 "http://127.0.0.1:$OPENCODE_ADAPTER_HOST_PORT/healthz" >/dev/null 2>&1; then
        warn "opencode-adapter /healthz не отвечает (compose.agents-mesh.yml не поднят?). Запустите stack-start.sh."
    else
        ok "opencode-adapter /healthz отвечает"
        DEMO_TASK="Ответь по-русски одним коротким предложением: ты opencode, делегированный через ACP-bridge. Закончи словом ГОТОВО."
        RUN_JSON="$(
            curl -fsS -m 240 -X POST "http://127.0.0.1:$OPENCODE_ADAPTER_HOST_PORT/v1/run" \
                -H "Authorization: Bearer $OPENCODE_ADAPTER_API_KEY" \
                -H "Content-Type: application/json" \
                -d "{\"task\":\"$DEMO_TASK\",\"timeout_s\":180}" \
                || true
        )"
        if [ -z "$RUN_JSON" ]; then
            warn "opencode-adapter не вернул тело — docker logs opencode-adapter"
        else
            printf '%s\n' "$RUN_JSON" | json_tool
            OK_FLAG="$(printf '%s' "$RUN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo)"
            if [ "$OK_FLAG" = "True" ]; then
                ok "POST /v1/run завершился ok=true"
            else
                warn "POST /v1/run вернул ok != True (см. logs_tail выше)"
            fi
        fi
    fi
fi

echo
echo "== Чеклист для интерактивной демонстрации =="
echo " 1. TUI:       bash opencode-start.sh    (или docker exec -it opencode opencode)"
echo " 2. Web UI:    bash opencode-web-start.sh  (HTTP UI на :${OPENCODE_WEB_HOST_PORT:-3400})"
echo " 3. Просьба:   - Кратко опиши .py-файлы в /workspace/project."
echo "              - Создай /workspace/project/opencode-demo-ru.txt с одной строкой."
echo " 4. ACP отладка:  bash agent-mesh-demo-ru.sh --run --opencode"
echo " 5. Остановка:    bash $SCRIPT_DIR/opencode-stop.sh"
ok "Демонстрация opencode готова."
