#!/usr/bin/env bash
# Демо/smoke агент-меша (Track 1 «agent as tool» + Track 2 «LiteLLM A2A»).
#
# Стек к моменту запуска уже должен быть поднят: bash stack-start.sh
# (compose.phoenix.yml, compose.openwebui.yml, compose.searchbox.yml и
# compose.agents-mesh.yml — последний — если оба *_ADAPTER_API_KEY заданы
# в .env, см. docs/agent-mesh.md).
#
# Что проверяет:
#   1. Docker daemon, сеть llm-stack-net, переменные окружения.
#   2. Адаптеры: /healthz, /openapi.json (+bearer), cycle-guard (X-Agent-Mesh-Depth → 429).
#   3. Track 1 (опционально, --run): живой POST /v1/run на каждом адаптере.
#   4. Track 2: LiteLLM /v1/models публикует agent/clawcode и agent/openhands;
#      опционально (--run) headless chat.completions через LiteLLM.
#   5. Cross-wiring (опционально, --check-mcp): что Claw и OpenHands видят MCP
#      адаптеры друг друга и НЕ видят свой собственный (cycle-guard на уровне
#      конфигурации MCP_SERVERS).
#   6. Чек-лист для живого демо в OpenWebUI / Claw / OpenHands UI.
#
# Запуск:
#   bash ./agent-mesh-demo-ru.sh                    # readiness без живых вызовов
#   bash ./agent-mesh-demo-ru.sh --run              # + один реальный POST /v1/run
#                                                   #   на clawcode-adapter и
#                                                   #   chat через agent/clawcode
#                                                   #   (OpenHands пропускается:
#                                                   #   ~3 GB pull + ~30 s warm-up)
#   bash ./agent-mesh-demo-ru.sh --run --openhands  # + реальный run на openhands-adapter
#   bash ./agent-mesh-demo-ru.sh --check-mcp        # + статика MCP_SERVERS из .env.*

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OPENWEBUI="$SCRIPT_DIR/.env.openwebui"
ENV_CLAWCODE="$SCRIPT_DIR/.env.clawcode"
ENV_OPENHANDS="$SCRIPT_DIR/.env.openhands"
ENV_OPENCODE="$SCRIPT_DIR/.env.opencode"

DO_RUN=0
DO_OPENHANDS=0
DO_OPENCODE=0
DO_CHECK_MCP=0

for arg in "$@"; do
    case "$arg" in
        --run) DO_RUN=1 ;;
        --openhands) DO_OPENHANDS=1 ;;
        --opencode) DO_OPENCODE=1 ;;
        --check-mcp) DO_CHECK_MCP=1 ;;
        -h|--help)
            cat <<USAGE
Usage: bash $0 [--run] [--openhands] [--opencode] [--check-mcp]
  --run        выполнить один POST /v1/run на clawcode-adapter и одну
               chat-completion через agent/clawcode (Track 2). Без --openhands
               прогон OpenHands пропускается (3 ГБ runtime-sandbox); без
               --opencode прогон opencode-adapter пропускается (требует, чтобы
               был поднят opencode-контейнер: bash opencode-start.sh --no-attach).
  --openhands  включить также реальный прогон openhands-adapter (требует --run).
  --opencode   включить реальный прогон opencode-adapter (требует --run).
  --check-mcp  проверить, что .env.clawcode/.env.openhands/.env.opencode не
               дают агенту видеть собственный адаптер (cycle-guard на уровне
               конфигурации).
USAGE
            exit 0
            ;;
    esac
done

_AM_KEYS=(
    LITELLM_HOST_PORT
    LITELLM_API_KEY
    PHOENIX_HOST_PORT
    OPENWEBUI_HOST_PORT
    CLAWCODE_ADAPTER_API_KEY
    OPENHANDS_ADAPTER_API_KEY
    OPENCODE_ADAPTER_API_KEY
    CLAWCODE_ADAPTER_HOST_PORT
    OPENHANDS_ADAPTER_HOST_PORT
    OPENCODE_ADAPTER_HOST_PORT
    MAX_NESTED_AGENT_CALLS
    CLAWCODE_DEFAULT_MODEL
    CLAWCODE_MCP_SERVERS
    OPENHANDS_MCP_SERVERS
    OPENCODE_MCP_SERVERS
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

load_selected_env "$ENV_FILE"        "${_AM_KEYS[@]}"
load_selected_env "$ENV_OPENWEBUI"   "${_AM_KEYS[@]}"
load_selected_env "$ENV_CLAWCODE"    "${_AM_KEYS[@]}"
load_selected_env "$ENV_OPENHANDS"   "${_AM_KEYS[@]}"
load_selected_env "$ENV_OPENCODE"    "${_AM_KEYS[@]}"

LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
PHOENIX_HOST_PORT="${PHOENIX_HOST_PORT:-6006}"
OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
CLAWCODE_ADAPTER_HOST_PORT="${CLAWCODE_ADAPTER_HOST_PORT:-8790}"
OPENHANDS_ADAPTER_HOST_PORT="${OPENHANDS_ADAPTER_HOST_PORT:-8791}"
OPENCODE_ADAPTER_HOST_PORT="${OPENCODE_ADAPTER_HOST_PORT:-8798}"
MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}"
CLAWCODE_DEFAULT_MODEL="${CLAWCODE_DEFAULT_MODEL:-openai/qwen3.6-35b-heretic}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }
hdr()  { echo; echo -e "\033[1;36m== $* ==\033[0m"; }

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

hdr "0. Окружение"

if ! docker info >/dev/null 2>&1; then
    die "Docker недоступен. Добавьте пользователя в группу docker и перелогиньтесь."
fi
ok "Docker daemon отвечает"

if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    die "Сеть llm-stack-net не найдена. Сначала: bash \"$SCRIPT_DIR/stack-start.sh\""
fi
ok "Сеть llm-stack-net существует"

if [ -z "${CLAWCODE_ADAPTER_API_KEY:-}" ] || [ -z "${OPENHANDS_ADAPTER_API_KEY:-}" ]; then
    die "Не заданы CLAWCODE_ADAPTER_API_KEY / OPENHANDS_ADAPTER_API_KEY в .env. Сгенерируйте: openssl rand -hex 32"
fi
ok "bearer-токены адаптеров (clawcode, openhands) заданы (.env)"
if [ -z "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    warn "OPENCODE_ADAPTER_API_KEY не задан — opencode-adapter будет пропущен."
fi

hdr "1. Адаптеры (Track 1: agent-as-tool)"

ADAPTERS_LIST=(
    "clawcode-adapter:${CLAWCODE_ADAPTER_HOST_PORT}:${CLAWCODE_ADAPTER_API_KEY}"
    "openhands-adapter:${OPENHANDS_ADAPTER_HOST_PORT}:${OPENHANDS_ADAPTER_API_KEY}"
)
if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    ADAPTERS_LIST+=("opencode-adapter:${OPENCODE_ADAPTER_HOST_PORT}:${OPENCODE_ADAPTER_API_KEY}")
fi

for adapter in "${ADAPTERS_LIST[@]}"; do
    name="${adapter%%:*}"
    rest="${adapter#*:}"
    port="${rest%%:*}"
    key="${rest#*:}"

    if ! curl -fsS -m 5 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
        warn "$name /healthz не отвечает на :$port (compose.agents-mesh.yml не запущен?)"
        echo "    docker compose --env-file .env -f compose.agents-mesh.yml up -d"
        continue
    fi
    ok "$name /healthz отвечает (http://127.0.0.1:$port/healthz)"

    OPENAPI_STATUS="$(curl -s -o /tmp/agent-mesh-demo-openapi.$$.json -m 5 -w "%{http_code}" \
        -H "Authorization: Bearer $key" \
        "http://127.0.0.1:$port/openapi.json" || echo "000")"
    if [ "$OPENAPI_STATUS" = "200" ]; then
        TOOL_PATHS="$(python3 -c '
import json, sys
try:
    data = json.load(open("/tmp/agent-mesh-demo-openapi.'"$$"'.json"))
    print(",".join(sorted((data.get("paths") or {}).keys())))
except Exception:
    print("")
' 2>/dev/null || true)"
        ok "$name /openapi.json (bearer): routes [$TOOL_PATHS]"
    else
        warn "$name /openapi.json вернул HTTP $OPENAPI_STATUS"
    fi
    rm -f /tmp/agent-mesh-demo-openapi.$$.json

    GUARD_STATUS="$(curl -s -o /dev/null -m 5 -w "%{http_code}" \
        -X POST "http://127.0.0.1:$port/v1/run" \
        -H "Authorization: Bearer $key" \
        -H "Content-Type: application/json" \
        -H "X-Agent-Mesh-Depth: $MAX_NESTED_AGENT_CALLS" \
        -d '{"task":"cycle-guard probe","timeout_s":10}' || echo "000")"
    if [ "$GUARD_STATUS" = "429" ]; then
        ok "$name cycle-guard сработал (X-Agent-Mesh-Depth=$MAX_NESTED_AGENT_CALLS → HTTP 429)"
    else
        warn "$name cycle-guard вернул HTTP $GUARD_STATUS (ожидалось 429)"
    fi
done

hdr "2. LiteLLM публикует A2A-алиасы (Track 2)"

LITELLM_MODELS_URL="http://localhost:$LITELLM_HOST_PORT/v1/models"
LITELLM_MODELS_JSON="$(curl -fsS -m 10 "$LITELLM_MODELS_URL" \
    -H "Authorization: Bearer $LITELLM_API_KEY" 2>/dev/null || true)"
[ -n "$LITELLM_MODELS_JSON" ] || die "LiteLLM $LITELLM_MODELS_URL не отвечает. Запустите stack-start.sh."
ok "LiteLLM /v1/models отвечает"

ALIAS_LIST=(agent/clawcode agent/openhands)
if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    ALIAS_LIST+=(agent/opencode)
fi
for alias in "${ALIAS_LIST[@]}"; do
    if printf '%s' "$LITELLM_MODELS_JSON" | grep -q "\"id\":\"$alias\""; then
        ok "LiteLLM публикует модель $alias"
    else
        warn "В /v1/models нет $alias — выполните: bash scripts/litellm-register-agent-mesh.sh"
    fi
done

if [ "$DO_CHECK_MCP" = "1" ]; then
    hdr "3. Cross-wiring MCP (статика .env.clawcode / .env.openhands)"
    python3 - <<PY
import json, os, sys
def show(label, raw):
    if not raw:
        print(f"!  {label}: пусто")
        return
    try:
        items = json.loads(raw)
    except Exception as exc:
        print(f"!  {label}: не парсится ({exc!r})")
        return
    names = [item.get("name") for item in items if isinstance(item, dict)]
    print(f"i  {label}: {', '.join(names) or '∅'}")
    return names

cc = show("Claw сидит на MCP", os.environ.get("CLAWCODE_MCP_SERVERS", "")) or []
oh = show("OpenHands сидит на MCP", os.environ.get("OPENHANDS_MCP_SERVERS", "")) or []
oc = show("opencode сидит на MCP", os.environ.get("OPENCODE_MCP_SERVERS", "")) or []

ok = True
if "clawcode-adapter" in cc:
    print("✗  Claw видит свой собственный адаптер — self-loop! Уберите clawcode-adapter из CLAWCODE_MCP_SERVERS")
    ok = False
if "openhands-adapter" in oh:
    print("✗  OpenHands видит свой собственный адаптер — self-loop! Уберите openhands-adapter из OPENHANDS_MCP_SERVERS")
    ok = False
if "opencode-adapter" in oc:
    print("✗  opencode видит свой собственный адаптер — self-loop! Уберите opencode-adapter из OPENCODE_MCP_SERVERS")
    ok = False
if ok:
    print("✓  Cross-wiring выглядит корректно (никто не видит сам себя)")
PY
fi

if [ "$DO_RUN" = "1" ]; then
    hdr "4. Live: POST /v1/run → clawcode-adapter (Track 1)"
    DEMO_TASK="Ответь по-русски одним коротким предложением: ты Claw Code, делегированный из теста agent-mesh. Закончи словом ГОТОВО."
    RUN_JSON="$(
        curl -fsS -m 180 -X POST "http://127.0.0.1:$CLAWCODE_ADAPTER_HOST_PORT/v1/run" \
            -H "Authorization: Bearer $CLAWCODE_ADAPTER_API_KEY" \
            -H "Content-Type: application/json" \
            -d "{\"task\":\"$DEMO_TASK\",\"timeout_s\":120}" \
            || true
    )"
    if [ -z "$RUN_JSON" ]; then
        warn "clawcode-adapter не вернул тело — смотрите docker logs clawcode-adapter и docker logs clawcode"
    else
        printf '%s\n' "$RUN_JSON" | json_tool
        OK_FLAG="$(printf '%s' "$RUN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo)"
        if [ "$OK_FLAG" = "True" ]; then
            ok "Track 1: clawcode-adapter завершил задачу успешно"
        else
            warn "Track 1: clawcode-adapter вернул ok != True (см. поле error/logs_tail выше)"
        fi
    fi

    hdr "5. Live: chat.completions через agent/clawcode (Track 2 / LiteLLM)"
    DEMO_PROMPT="Ответь одним коротким предложением по-русски: ты идёшь через LiteLLM-алиас agent/clawcode и затем в адаптер. Закончи словом ГОТОВО."
    CHAT_JSON="$(
        curl -fsS -m 180 -X POST "http://localhost:$LITELLM_HOST_PORT/v1/chat/completions" \
            -H "Authorization: Bearer $LITELLM_API_KEY" \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"agent/clawcode\",\"messages\":[{\"role\":\"user\",\"content\":\"$DEMO_PROMPT\"}],\"temperature\":0.3}" \
            || true
    )"
    if [ -z "$CHAT_JSON" ]; then
        warn "LiteLLM не вернул тело для agent/clawcode (смотрите docker logs litellm)"
    else
        printf '%s\n' "$CHAT_JSON" | json_tool
        DEMO_TEXT="$(printf '%s' "$CHAT_JSON" | extract_chat_content 2>/dev/null || true)"
        if [ -n "$DEMO_TEXT" ]; then
            ok "Track 2: LiteLLM проксировал ответ через agent/clawcode"
        else
            warn "Track 2: ответ есть, content не извлечён — смотрите JSON выше"
        fi
    fi

    if [ "$DO_OPENCODE" = "1" ] && [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
        hdr "5b. Live: POST /v1/run → opencode-adapter (ACP session/new + session/prompt)"
        if ! docker inspect --format='{{.State.Status}}' opencode 2>/dev/null | grep -q running; then
            warn "Контейнер opencode не запущен — пропускаю прогон. Запустите: bash $SCRIPT_DIR/opencode-start.sh --no-attach"
        else
            OC_TASK="Ответь по-русски одним коротким предложением: ты opencode (ACP), делегированный из теста agent-mesh. Закончи словом ГОТОВО."
            OC_JSON="$(
                curl -fsS -m 240 -X POST "http://127.0.0.1:$OPENCODE_ADAPTER_HOST_PORT/v1/run" \
                    -H "Authorization: Bearer $OPENCODE_ADAPTER_API_KEY" \
                    -H "Content-Type: application/json" \
                    -d "{\"task\":\"$OC_TASK\",\"timeout_s\":180}" \
                    || true
            )"
            if [ -z "$OC_JSON" ]; then
                warn "opencode-adapter не вернул тело — docker logs opencode-adapter / docker logs opencode"
            else
                printf '%s\n' "$OC_JSON" | json_tool
                OC_OK="$(printf '%s' "$OC_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo)"
                if [ "$OC_OK" = "True" ]; then
                    ok "Track 1: opencode-adapter завершил ACP-сессию успешно"
                else
                    warn "Track 1: opencode-adapter вернул ok != True (поле error/logs_tail выше)"
                fi
            fi
        fi
    elif [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
        warn "Прогон opencode-adapter пропущен (добавьте --opencode к команде)."
    fi

    if [ "$DO_OPENHANDS" = "1" ]; then
        hdr "6. Live: POST /v1/run → openhands-adapter (3 ГБ runtime-sandbox, ~30 s warm-up)"
        OH_TASK="Ответь по-русски одним коротким предложением: ты OpenHands из agent-mesh теста. Закончи словом ГОТОВО."
        OH_JSON="$(
            curl -fsS -m 1800 -X POST "http://127.0.0.1:$OPENHANDS_ADAPTER_HOST_PORT/v1/run" \
                -H "Authorization: Bearer $OPENHANDS_ADAPTER_API_KEY" \
                -H "Content-Type: application/json" \
                -d "{\"task\":\"$OH_TASK\",\"timeout_s\":900}" \
                || true
        )"
        if [ -z "$OH_JSON" ]; then
            warn "openhands-adapter не вернул тело (docker logs openhands-adapter)"
        else
            printf '%s\n' "$OH_JSON" | json_tool
            OH_OK="$(printf '%s' "$OH_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo)"
            if [ "$OH_OK" = "True" ]; then
                ok "Track 1: openhands-adapter завершил задачу успешно"
            else
                warn "Track 1: openhands-adapter вернул ok != True (см. поле error/logs_tail)"
            fi
        fi
    else
        warn "Прогон openhands-adapter пропущен (добавьте --openhands к команде)."
    fi
else
    warn "Без флага --run живые вызовы /v1/run и agent/clawcode пропущены."
    echo "    Полный прогон: bash $0 --run            (Claw + Track 2)"
    echo "                  bash $0 --run --openhands (включая OpenHands)"
    echo "                  bash $0 --run --opencode  (включая opencode/ACP)"
fi

hdr "Чек-лист для живого демо в браузере"
cat <<DEMO
1. OpenWebUI как «диспетчер»  →  http://localhost:$OPENWEBUI_HOST_PORT
   • Войдите admin'ом, откройте чат, в поле «➕» включите два tool-сервера:
     clawcode-adapter и openhands-adapter (предварительно зарегистрированы
     stack-start.sh / scripts/openwebui-register-agent-mesh.sh).
   • В выбранной модели (qwen3.6-35b-heretic) попросите:
       «Делегируй задачу clawcode-adapter: проверь, какие .py-файлы лежат
        в /workspace/project и кратко опиши их назначение по-русски.»
   • LLM сам выберет tool, дёрнет POST /v1/run, выведет result_text.

2. OpenWebUI как «прямой клиент A2A»  →  тот же чат
   • Смените модель на agent/clawcode (или agent/openhands).
   • Любое сообщение пойдёт LiteLLM → адаптер → headless-агент.
   • Полезно для сравнения «модельного» Track 2 с «tool»-Track 1.

3. Claw Code REPL зовёт OpenHands как MCP-tool
       bash $SCRIPT_DIR/clawcode-start.sh
       claw> Используя MCP-tool 'openhands-adapter' (run_openhands), попроси
             OpenHands создать /workspace/project/from-claw.txt со строкой
             «привет от Claw через OpenHands».
   • Cycle-guard: Claw НЕ видит run_clawcode (см. CLAWCODE_MCP_SERVERS).

4. OpenHands UI (:3300) зовёт Claw через MCP-tool
       OpenHands → New conversation → промпт по-русски:
       «Через MCP-tool clawcode-adapter (run_clawcode) попроси Claw Code
        создать /workspace/project/from-oh.txt со строкой
        «привет от OpenHands через Claw».»
   • Cycle-guard: OpenHands НЕ видит run_openhands (см. OPENHANDS_MCP_SERVERS).

5. Phoenix  →  http://localhost:$PHOENIX_HOST_PORT
   • Выберите проект qwen3.6-heretic.
   • После каждого живого запроса видны три цепочки span'ов:
       LiteLLM (chat.completion) → agent_mesh.run.<agent_id> → внутренние шаги.
   • Track 2 (agent/clawcode) даёт цепочку из LiteLLM proxy span + adapter span.

Полная справка:  docs/agent-mesh.md
Smoke-проверка:  bash stack-smoke.sh   (раздел «D. agent-mesh adapters»)
DEMO

ok "Демо agent-mesh готово."
