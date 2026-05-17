#!/usr/bin/env bash
# Единый bootstrap для стека: Phoenix + Postgres + LiteLLM + LocalAI + OpenWebUI.
# Идемпотентен: безопасно перезапускать. Порядок соответствует разделу 6 плана.
#
# Предусловие: пользователь в группе docker (см. localai-start.sh — при первом запуске он
# добавляет и перезапускается через sg docker). Если docker info не работает, скрипт
# подскажет, что делать.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.llamacpp}"
OPENWEBUI_ENV_FILE="$SCRIPT_DIR/.env.openwebui"
COMPOSE_PHOENIX="$SCRIPT_DIR/compose.phoenix.yml"
COMPOSE_LLAMA="$SCRIPT_DIR/compose.llama.yml"
COMPOSE_LOCALAI="$SCRIPT_DIR/compose.localai.yml"
COMPOSE_LOCALAI_QWEN="$SCRIPT_DIR/compose.localai.qwen36.yml"
COMPOSE_OPENWEBUI="$SCRIPT_DIR/compose.openwebui.yml"
COMPOSE_SEARXNG="$SCRIPT_DIR/compose.searxng.yml"
COMPOSE_SHELLBOX="$SCRIPT_DIR/compose.shellbox.yml"
COMPOSE_FSBOX="$SCRIPT_DIR/compose.fsbox.yml"
COMPOSE_SEARCHBOX="$SCRIPT_DIR/compose.searchbox.yml"
COMPOSE_AGENT_MESH="$SCRIPT_DIR/compose.agents-mesh.yml"
JUPYTER_HOST_START="$SCRIPT_DIR/jupyter-host-start.sh"
AGENT_DEV_DIR="${AGENT_DEV_DIR:-$HOME/agent_dev}"

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
    LOCALAI_HOST_PORT \
    LOCALAI_ENABLE_QWEN36 \
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    LITELLM_UI_USERNAME \
    LITELLM_UI_PASSWORD \
    LLAMA_HOST_PORT \
    PHOENIX_HOST_PORT \
    LLM_BACKEND \
    LMSTUDIO_HOST_PORT \
    LMSTUDIO_API_BASE \
    LMSTUDIO_API_KEY \
    LMSTUDIO_MODEL_ID \
    LLAMA_CPP_HOST_PORT \
    LLAMA_CPP_API_BASE \
    LLAMA_CPP_API_KEY \
    LOCALAI_IMAGE \
    CLAWCODE_ADAPTER_API_KEY \
    OPENHANDS_ADAPTER_API_KEY \
    CLAWCODE_ADAPTER_HOST_PORT \
    OPENHANDS_ADAPTER_HOST_PORT \
    CLAWCODE_ADAPTER_GRPC_HOST_PORT \
    OPENHANDS_ADAPTER_GRPC_HOST_PORT \
    MAX_NESTED_AGENT_CALLS \
    AGENT_MESH_ENABLED \
    AGENT_REGISTRY_API_KEY \
    AGENT_REGISTRY_HOST_PORT \
    AGENT_REGISTRY_AGENTS \
    SKILLS_MANAGER_API_KEY \
    SKILLS_MANAGER_HOST_PORT \
    SKILLS_ADAPTERS \
    A2A_BINDINGS \
    A2A_TASK_STORE_DSN \
    A2A_PUSH_MAX_RETRIES \
    A2A_PUSH_BACKOFF_BASE_S

load_selected_env "$ENV_OVERRIDE_FILE" \
    LOCALAI_HOST_PORT \
    LOCALAI_ENABLE_QWEN36 \
    LITELLM_HOST_PORT \
    LITELLM_API_KEY \
    LITELLM_UI_USERNAME \
    LITELLM_UI_PASSWORD \
    LLAMA_HOST_PORT \
    PHOENIX_HOST_PORT \
    LLM_BACKEND \
    LMSTUDIO_HOST_PORT \
    LMSTUDIO_API_BASE \
    LMSTUDIO_API_KEY \
    LMSTUDIO_MODEL_ID \
    LLAMA_CPP_HOST_PORT \
    LLAMA_CPP_API_BASE \
    LLAMA_CPP_API_KEY \
    LOCALAI_IMAGE

load_selected_env "$OPENWEBUI_ENV_FILE" \
    OPENWEBUI_ENABLED \
    OPENWEBUI_HOST_PORT \
    WEBUI_SECRET_KEY \
    OPENWEBUI_ENABLE_PERSISTENT_CONFIG \
    OPENWEBUI_ENABLE_LOGIN_FORM \
    OPENWEBUI_ENABLE_SIGNUP \
    OPENWEBUI_ENABLE_API_KEYS \
    OPENWEBUI_ENABLE_OLLAMA_API \
    OPENWEBUI_ENABLE_OPENAI_API \
    OPENWEBUI_OPENAI_API_BASE_URLS \
    OPENWEBUI_OPENAI_API_KEYS \
    OPENWEBUI_DEFAULT_MODEL \
    OPENWEBUI_DEFAULT_LOCALE \
    OPENWEBUI_AUDIO_API_BASE_URL \
    OPENWEBUI_AUDIO_API_KEY \
    OPENWEBUI_AUDIO_STT_ENGINE \
    OPENWEBUI_AUDIO_STT_MODEL \
    OPENWEBUI_AUDIO_STT_CONTENT_TYPES \
    OPENWEBUI_AUDIO_TTS_ENGINE \
    OPENWEBUI_AUDIO_TTS_MODEL \
    OPENWEBUI_AUDIO_TTS_VOICE \
    OPENWEBUI_ADMIN_NAME \
    OPENWEBUI_ADMIN_EMAIL \
    OPENWEBUI_ADMIN_PASSWORD \
    OPENWEBUI_ENABLE_WEB_SEARCH \
    OPENWEBUI_WEB_SEARCH_ENGINE \
    OPENWEBUI_SEARXNG_QUERY_URL \
    OPENWEBUI_WEB_SEARCH_RESULT_COUNT \
    OPENWEBUI_WEB_SEARCH_CONCURRENT \
    SEARXNG_HOST_PORT \
    SEARXNG_BASE_URL \
    SEARXNG_INSTANCE_NAME \
    SEARXNG_SECRET \
    SEARXNG_UWSGI_WORKERS \
    SEARXNG_UWSGI_THREADS \
    OPENWEBUI_ENABLE_CODE_EXECUTION \
    OPENWEBUI_ENABLE_CODE_INTERPRETER \
    OPENWEBUI_CODE_EXECUTION_ENGINE \
    OPENWEBUI_CODE_EXECUTION_JUPYTER_URL \
    OPENWEBUI_CODE_EXECUTION_JUPYTER_AUTH \
    OPENWEBUI_CODE_EXECUTION_JUPYTER_TIMEOUT \
    OPENWEBUI_CODE_INTERPRETER_ENGINE \
    OPENWEBUI_CODE_INTERPRETER_JUPYTER_URL \
    OPENWEBUI_CODE_INTERPRETER_JUPYTER_AUTH \
    OPENWEBUI_CODE_INTERPRETER_JUPYTER_TIMEOUT \
    JUPYTER_TOKEN \
    JUPYTER_HOST \
    JUPYTER_PORT \
    JUPYTER_ROOT_DIR \
    SHELLBOX_HOST_PORT \
    SHELLBOX_API_KEY \
    FSBOX_HOST_PORT \
    FSBOX_API_KEY \
    SEARCHBOX_HOST_PORT \
    SEARCHBOX_MCP_HOST_PORT \
    SEARCHBOX_API_KEY \
    SEARCHBOX_SEARXNG_URL \
    SEARCHBOX_HTTP_TIMEOUT \
    SEARCHBOX_LOG_LEVEL \
    BRAVE_API_KEY \
    GOOGLE_API_KEY \
    GOOGLE_CX \
    GITHUB_TOKEN \
    TAVILY_API_KEY \
    STACKEXCHANGE_KEY \
    OPENALEX_MAILTO \
    OPENWEBUI_BANNERS \
    OPENWEBUI_OPENHANDS_HOST_PORT \
    OPENWEBUI_LITELLM_HOST_PORT

LOCALAI_HOST_PORT="${LOCALAI_HOST_PORT:-8180}"
LOCALAI_ENABLE_QWEN36="${LOCALAI_ENABLE_QWEN36:-0}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
LITELLM_UI_USERNAME="${LITELLM_UI_USERNAME:-admin}"
LITELLM_UI_PASSWORD="${LITELLM_UI_PASSWORD:-litellm-local-ui}"
LLAMA_HOST_PORT="${LLAMA_HOST_PORT:-8090}"
PHOENIX_HOST_PORT="${PHOENIX_HOST_PORT:-6006}"
LLM_BACKEND="${LLM_BACKEND:-lmstudio}"
LMSTUDIO_HOST_PORT="${LMSTUDIO_HOST_PORT:-1234}"
LMSTUDIO_MODEL_ID="${LMSTUDIO_MODEL_ID:-tvall43-qwen3.6-35b-a3b-heretic}"
LLAMA_CPP_HOST_PORT="${LLAMA_CPP_HOST_PORT:-8090}"
LLAMA_CPP_API_BASE="${LLAMA_CPP_API_BASE:-http://host.docker.internal:${LLAMA_CPP_HOST_PORT}/v1}"
LLAMA_CPP_API_KEY="${LLAMA_CPP_API_KEY:-llama-cpp}"
export LLAMA_CPP_API_BASE LLAMA_CPP_API_KEY
OPENWEBUI_ENABLED="${OPENWEBUI_ENABLED:-1}"
OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
OPENWEBUI_DEFAULT_MODEL="${OPENWEBUI_DEFAULT_MODEL:-qwen3.6-35b-heretic}"
OPENWEBUI_DEFAULT_LOCALE="${OPENWEBUI_DEFAULT_LOCALE:-ru-RU}"
OPENWEBUI_AUDIO_STT_MODEL="${OPENWEBUI_AUDIO_STT_MODEL:-stt-whisper-large-v3-turbo}"
OPENWEBUI_AUDIO_TTS_MODEL="${OPENWEBUI_AUDIO_TTS_MODEL:-tts-qwen3-1.7b}"
SEARXNG_HOST_PORT="${SEARXNG_HOST_PORT:-8081}"
SHELLBOX_HOST_PORT="${SHELLBOX_HOST_PORT:-8001}"
FSBOX_HOST_PORT="${FSBOX_HOST_PORT:-8002}"
SEARCHBOX_HOST_PORT="${SEARCHBOX_HOST_PORT:-8023}"
SEARCHBOX_MCP_HOST_PORT="${SEARCHBOX_MCP_HOST_PORT:-8024}"
JUPYTER_HOST="${JUPYTER_HOST:-127.0.0.1}"
JUPYTER_PORT="${JUPYTER_PORT:-8888}"

# ── LocalAI image selection: x86 CUDA image vs ARM64/L4T CUDA image ─────────
if [ -z "${LOCALAI_IMAGE:-}" ]; then
    ARCH="$(uname -m)"
    if [ "$ARCH" = "aarch64" ]; then
        CUDA_MAJOR="$(nvidia-smi 2>/dev/null | grep -oP 'CUDA Version: \K[0-9]+' | head -1 || echo 12)"
        if [ "$CUDA_MAJOR" -ge 13 ]; then
            export LOCALAI_IMAGE="localai/localai:latest-nvidia-l4t-arm64-cuda-13"
        else
            export LOCALAI_IMAGE="localai/localai:latest-nvidia-l4t-arm64"
        fi
    else
        export LOCALAI_IMAGE="localai/localai:latest-gpu-nvidia-cuda-12"
    fi
fi

say() { echo -e "\033[1;36m→\033[0m $*"; }
ok()  { echo -e "\033[1;32m✓\033[0m $*"; }
warn(){ echo -e "\033[1;33m!\033[0m $*"; }
die() { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

litellm_ready() {
    curl -fsS -m 5 \
        -H "Authorization: Bearer $LITELLM_API_KEY" \
        "http://localhost:$LITELLM_HOST_PORT/v1/models" >/dev/null
}

localai_ready() {
    curl -fsS -m 5 "http://localhost:$LOCALAI_HOST_PORT/readyz" >/dev/null
}

openwebui_ready() {
    curl -fsS -m 5 "http://localhost:$OPENWEBUI_HOST_PORT/health" >/dev/null
}

searxng_ready() {
    curl -fsS -m 5 "http://localhost:$SEARXNG_HOST_PORT/search?q=test&format=json" \
        | grep -q '"results"'
}

shellbox_ready() {
    if [ -z "${SHELLBOX_API_KEY:-}" ]; then
        return 1
    fi
    curl -fsS -m 5 \
        -H "Authorization: Bearer $SHELLBOX_API_KEY" \
        "http://127.0.0.1:$SHELLBOX_HOST_PORT/openapi.json" >/dev/null
}

fsbox_ready() {
    if [ -z "${FSBOX_API_KEY:-}" ]; then
        return 1
    fi
    curl -fsS -m 5 \
        -H "Authorization: Bearer $FSBOX_API_KEY" \
        "http://127.0.0.1:$FSBOX_HOST_PORT/openapi.json" >/dev/null
}

searchbox_ready() {
    if [ -z "${SEARCHBOX_API_KEY:-}" ]; then
        return 1
    fi
    curl -fsS -m 5 \
        -H "Authorization: Bearer $SEARCHBOX_API_KEY" \
        "http://127.0.0.1:$SEARCHBOX_HOST_PORT/openapi.json" >/dev/null
}

clawcode_adapter_ready() {
    curl -fsS -m 5 \
        "http://127.0.0.1:${CLAWCODE_ADAPTER_HOST_PORT:-8790}/healthz" >/dev/null
}

openhands_adapter_ready() {
    curl -fsS -m 5 \
        "http://127.0.0.1:${OPENHANDS_ADAPTER_HOST_PORT:-8791}/healthz" >/dev/null
}

opencode_adapter_ready() {
    curl -fsS -m 5 \
        "http://127.0.0.1:${OPENCODE_ADAPTER_HOST_PORT:-8798}/healthz" >/dev/null
}

agent_registry_ready() {
    curl -fsS -m 5 \
        "http://127.0.0.1:${AGENT_REGISTRY_HOST_PORT:-8794}/healthz" >/dev/null
}

skills_manager_ready() {
    curl -fsS -m 5 \
        "http://127.0.0.1:${SKILLS_MANAGER_HOST_PORT:-8795}/healthz" >/dev/null
}

jupyter_ready() {
    if [ -z "${JUPYTER_TOKEN:-}" ]; then
        return 1
    fi
    curl -fsS -m 5 "http://127.0.0.1:$JUPYTER_PORT/api?token=$JUPYTER_TOKEN" \
        | grep -q '"version"'
}

# ── 0. Docker доступен? ─────────────────────────────────────────────────────
if ! docker info &>/dev/null; then
    warn "docker info не работает. Добавьте пользователя в группу docker:"
    warn "    sudo usermod -aG docker \"$USER\" && newgrp docker"
    die "Docker недоступен"
fi

# ── 1. Сеть llm-stack-net ────────────────────────────────────────────────────
if ! docker network inspect llm-stack-net &>/dev/null; then
    say "Создаю сеть llm-stack-net"
    docker network create llm-stack-net >/dev/null
    ok "сеть создана"
else
    ok "сеть llm-stack-net уже есть"
fi

# ── 2. Cleanup старого Phoenix (если был ручной контейнер вне compose) ───────
say "Чищу следы старого Phoenix (контейнеры/volume'ы не из нашего compose)"
OLD_PHX_CONTAINERS=$(docker ps -a --format '{{.Names}} {{.Image}}' \
    | awk 'tolower($0) ~ /phoenix/ {print $1}' \
    | grep -vE '^(phoenix|phoenix-db)$' || true)
if [ -n "$OLD_PHX_CONTAINERS" ]; then
    echo "$OLD_PHX_CONTAINERS" | while read -r name; do
        [ -z "$name" ] && continue
        warn "удаляю старый контейнер: $name"
        docker rm -f "$name" >/dev/null || true
    done
else
    ok "сторонних phoenix-контейнеров нет"
fi
OLD_PHX_VOLUMES=$(docker volume ls --format '{{.Name}}' \
    | grep -i phoenix \
    | grep -vE '^(phoenix-pg-volume|phoenix-data-volume)$' || true)
if [ -n "$OLD_PHX_VOLUMES" ]; then
    echo "$OLD_PHX_VOLUMES" | while read -r v; do
        [ -z "$v" ] && continue
        warn "удаляю старый volume: $v"
        docker volume rm "$v" >/dev/null || true
    done
else
    ok "сторонних phoenix-volume'ов нет"
fi

# ── 3. Phoenix + Postgres + LiteLLM ──────────────────────────────────────────
say "Запускаю Phoenix + LiteLLM ($COMPOSE_PHOENIX)"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_PHOENIX" up -d

say "Жду UI Phoenix на :$PHOENIX_HOST_PORT"
for i in {1..60}; do
    if curl -fsS -m 2 "http://localhost:$PHOENIX_HOST_PORT" >/dev/null 2>&1; then
        ok "Phoenix UI отвечает"
        break
    fi
    sleep 2
    [ "$i" = 60 ] && die "Phoenix не поднялся за 120s (см. docker logs phoenix)"
done

say "Жду LiteLLM на :$LITELLM_HOST_PORT"
for i in {1..60}; do
    if litellm_ready; then
        ok "LiteLLM отвечает"
        break
    fi
    sleep 2
    [ "$i" = 60 ] && die "LiteLLM не поднялся за 120s (см. docker logs litellm)"
done

# ── 3b. SearXNG (web search backend for OpenWebUI) ───────────────────────────
if [ -f "$COMPOSE_SEARXNG" ]; then
    if [ -z "${SEARXNG_SECRET:-}" ]; then
        warn "SEARXNG_SECRET не задан в .env.openwebui — используется placeholder; задайте openssl rand -hex 32"
    fi
    say "Запускаю SearXNG ($COMPOSE_SEARXNG)"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_SEARXNG" up -d

    say "Жду SearXNG JSON API на :$SEARXNG_HOST_PORT"
    SEARXNG_OK=0
    for i in {1..45}; do
        if searxng_ready; then
            ok "SearXNG отвечает на /search?format=json"
            SEARXNG_OK=1
            break
        fi
        sleep 2
    done
    if [ "$SEARXNG_OK" != "1" ]; then
        warn "SearXNG не отвечает на JSON-search за 90s (см. docker logs searxng)"
    fi
fi

# ── 4. LLM backend: host-side OpenAI-compatible API or Docker llama-server ───
check_lmstudio_upstream() {
    say "Проверяю host-side LM Studio на :$LMSTUDIO_HOST_PORT"
    curl -fsS -m 5 "http://localhost:$LMSTUDIO_HOST_PORT/v1/models" >/dev/null \
        || die "host-side LM Studio API недоступен на :$LMSTUDIO_HOST_PORT (запустите LM Studio и загрузите модель)"
    if ! curl -fsS "http://localhost:$LMSTUDIO_HOST_PORT/v1/models" | grep -q "$LMSTUDIO_MODEL_ID"; then
        warn "Модель $LMSTUDIO_MODEL_ID не найдена в LM Studio /v1/models"
    fi
    ok "host-side LM Studio API отвечает"
}

check_llamacpp_upstream() {
    say "Проверяю host-side llama.cpp на :$LLAMA_CPP_HOST_PORT"
    curl -fsS -m 5 "http://localhost:$LLAMA_CPP_HOST_PORT/v1/models" >/dev/null \
        || die "host-side llama.cpp API недоступен на :$LLAMA_CPP_HOST_PORT (запустите llamacpp-host-start.sh)"
    ok "host-side llama.cpp API отвечает"
}

case "$LLM_BACKEND" in
    lmstudio)
        check_lmstudio_upstream
        ;;
    llamacpp)
        check_llamacpp_upstream
        ;;
    both)
        say "LLM_BACKEND=both: проверяю и LM Studio, и llama.cpp"
        check_lmstudio_upstream
        check_llamacpp_upstream
        ;;
    llama)
        say "Собираю и запускаю llama-server ($COMPOSE_LLAMA). Первая сборка — 15–25 мин."
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_LLAMA" up -d --build

        say "Жду healthcheck llama-server"
        for i in {1..120}; do
            status=$(docker inspect --format='{{.State.Health.Status}}' llama-server 2>/dev/null || echo "none")
            if [ "$status" = "healthy" ]; then
                ok "llama-server healthy"
                break
            fi
            sleep 5
            [ "$i" = 120 ] && warn "llama-server не стал healthy за 600s — проверьте: docker logs llama-server --tail 80"
        done
        ;;
    *)
        die "Неизвестный LLM_BACKEND=$LLM_BACKEND (ожидается lmstudio, llamacpp, both или llama)"
        ;;
esac

# ── 5. LocalAI (базовый профиль + опциональный legacy qwen3.6) ──────────────
say "Запускаю LocalAI ($COMPOSE_LOCALAI)"
say "LocalAI image: $LOCALAI_IMAGE"
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    warn "Legacy LocalAI-профиль qwen3.6-35b-heretic включён вручную"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_LOCALAI" -f "$COMPOSE_LOCALAI_QWEN" up -d
else
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_LOCALAI" up -d
fi

say "Жду LocalAI на :$LOCALAI_HOST_PORT"
for i in {1..90}; do
    if localai_ready; then
        ok "LocalAI отвечает"
        break
    fi
    sleep 2
    [ "$i" = 90 ] && die "LocalAI не поднялся за 180s (см. docker logs localai)"
done

if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    say "Пропускаю удаление qwen3.6-35b-heretic из LocalAI"
else
    say "Удаляю qwen3.6-35b-heretic из реестра LocalAI (если присутствует)"
    if localai_ready; then
        for model in qwen3.6-35b-heretic tvall43-qwen3.6-35b-a3b-heretic; do
            resp=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                "http://localhost:$LOCALAI_HOST_PORT/models/delete/$model" || echo "000")
            case "$resp" in
                2*) ok "LocalAI: удалён $model (HTTP $resp)";;
                404) ok "LocalAI: $model отсутствует (HTTP 404) — OK";;
                *)   warn "LocalAI: delete $model → HTTP $resp";;
            esac
        done
    else
        warn "LocalAI /readyz не отвечает — пропускаю удаление из реестра"
    fi
fi

LOCALAI_MODELS_JSON="$(curl -fsS -m 10 "http://localhost:$LOCALAI_HOST_PORT/v1/models" 2>/dev/null || true)"
if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_STT_MODEL\""; then
    ok "LocalAI audio: найден STT alias $OPENWEBUI_AUDIO_STT_MODEL"
else
    warn "LocalAI audio: не найден STT alias $OPENWEBUI_AUDIO_STT_MODEL"
    warn "OpenWebUI останется доступен для чата; при отсутствии backend STT используйте browser/Web API fallback"
fi
if printf '%s' "$LOCALAI_MODELS_JSON" | grep -q "\"id\":\"$OPENWEBUI_AUDIO_TTS_MODEL\""; then
    ok "LocalAI audio: найден TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
else
    warn "LocalAI audio: не найден TTS alias $OPENWEBUI_AUDIO_TTS_MODEL"
    warn "OpenWebUI останется доступен для чата; при отсутствии backend TTS используйте browser/Web API fallback"
fi

# ── 6. OpenWebUI ─────────────────────────────────────────────────────────────
if [ "$OPENWEBUI_ENABLED" = "1" ]; then
    [ -n "${WEBUI_SECRET_KEY:-}" ] || die "WEBUI_SECRET_KEY не задан (ожидается в .env.openwebui)"

    # Pre-seed external OpenAPI tool servers (shellbox + fsbox + searchbox +
    # agent-mesh adapters) on first boot only. Keys that are empty produce no
    # entry, so unset/blank adapter tokens simply skip those registrations.
    export OPENWEBUI_TOOL_SERVER_CONNECTIONS="$(
        SHELLBOX_API_KEY="${SHELLBOX_API_KEY:-}" \
        FSBOX_API_KEY="${FSBOX_API_KEY:-}" \
        SEARCHBOX_API_KEY="${SEARCHBOX_API_KEY:-}" \
        CLAWCODE_ADAPTER_API_KEY="${CLAWCODE_ADAPTER_API_KEY:-}" \
        OPENHANDS_ADAPTER_API_KEY="${OPENHANDS_ADAPTER_API_KEY:-}" \
        OPENCODE_ADAPTER_API_KEY="${OPENCODE_ADAPTER_API_KEY:-}" \
        AGENT_REGISTRY_API_KEY="${AGENT_REGISTRY_API_KEY:-}" \
        SKILLS_MANAGER_API_KEY="${SKILLS_MANAGER_API_KEY:-}" \
        MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}" \
        python3 - <<'PY'
import json, os
conn = []
shell_key = os.environ.get("SHELLBOX_API_KEY", "")
if shell_key:
    conn.append({
        "type": "openapi",
        "url": "http://shellbox:8001",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": shell_key,
        "config": {"enable": True},
        "info": {
            "id": "shellbox",
            "name": "shellbox",
            "description": "Read-only CLI tools (mcp-shell-server via mcpo, /workspace mounted ro).",
        },
    })
fs_key = os.environ.get("FSBOX_API_KEY", "")
if fs_key:
    conn.append({
        "type": "openapi",
        "url": "http://fsbox:8001",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": fs_key,
        "config": {"enable": True},
        "info": {
            "id": "fsbox",
            "name": "fsbox",
            "description": "Filesystem MCP (typed read/write/edit/list/search) over the shared agent workspace (default: ~/agent_dev, rw).",
        },
    })
search_key = os.environ.get("SEARCHBOX_API_KEY", "")
if search_key:
    conn.append({
        "type": "openapi",
        "url": "http://searchbox:8001",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": search_key,
        "config": {"enable": True},
        "info": {
            "id": "searchbox",
            "name": "searchbox",
            "description": (
                "MCP Search: SearXNG, Wikipedia/Wikidata, arXiv, GitHub, "
                "Hacker News, StackExchange, Crossref/OpenAlex, PyPI/NPM, "
                "DuckDuckGo, + optional Brave/Google/Tavily."
            ),
        },
    })
max_nested = os.environ.get("MAX_NESTED_AGENT_CALLS", "1")
clawcode_adapter_key = os.environ.get("CLAWCODE_ADAPTER_API_KEY", "")
if clawcode_adapter_key:
    conn.append({
        "type": "openapi",
        "url": "http://clawcode-adapter:8790",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": clawcode_adapter_key,
        "config": {"enable": True},
        "info": {
            "id": "clawcode-adapter",
            "name": "clawcode-adapter",
            "description": (
                "Delegate a self-contained coding task to the headless Claw "
                "Code (Rust CLI) agent. One call = one task; never call "
                f"yourself (cycle guard, max_nested_agent_calls={max_nested}). "
                "Endpoints: POST /v1/run, POST /v1/sessions[/{id}/messages]."
            ),
        },
    })
openhands_adapter_key = os.environ.get("OPENHANDS_ADAPTER_API_KEY", "")
if openhands_adapter_key:
    conn.append({
        "type": "openapi",
        "url": "http://openhands-adapter:8791",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": openhands_adapter_key,
        "config": {"enable": True},
        "info": {
            "id": "openhands-adapter",
            "name": "openhands-adapter",
            "description": (
                "Delegate a self-contained coding task to the headless "
                "OpenHands 1.6 agent (ephemeral runtime sandbox per call). "
                "One call = one task; never call yourself (cycle guard, "
                f"max_nested_agent_calls={max_nested}). Endpoints: "
                "POST /v1/run, POST /v1/sessions[/{id}/messages]."
            ),
        },
    })
opencode_adapter_key = os.environ.get("OPENCODE_ADAPTER_API_KEY", "")
if opencode_adapter_key:
    conn.append({
        "type": "openapi",
        "url": "http://opencode-adapter:8798",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": opencode_adapter_key,
        "config": {"enable": True},
        "info": {
            "id": "opencode-adapter",
            "name": "opencode-adapter",
            "description": (
                "Delegate a self-contained coding task to opencode "
                "(https://opencode.ai/) bridged via ACP (JSON-RPC over "
                "`docker exec opencode opencode acp`). One call = one "
                "ACP session/prompt turn with native LSP + terminal "
                "tools. Never call yourself (cycle guard, "
                f"max_nested_agent_calls={max_nested}). Endpoints: "
                "POST /v1/run, POST /v1/sessions[/{id}/messages]."
            ),
        },
    })
agent_registry_key = os.environ.get("AGENT_REGISTRY_API_KEY", "")
if agent_registry_key:
    conn.append({
        "type": "openapi",
        "url": "http://agent-registry:8000",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": agent_registry_key,
        "config": {"enable": True},
        "info": {
            "id": "agent-registry",
            "name": "agent-registry",
            "description": (
                "A2A Agent Registry: single tool-server fronting every "
                "registered agent adapter. Discover agents via GET "
                "/v1/agents (returns JWS-signed AgentCards), send/list/"
                "cancel/subscribe Tasks via /v1/agents/{id}/message:send"
                "|stream, /v1/tasks[?agents=*], /v1/tasks/{agentId}:{"
                "taskId}:cancel|subscribe. Prefer this over wiring each "
                "adapter as a separate tool-server."
            ),
        },
    })
skills_manager_key = os.environ.get("SKILLS_MANAGER_API_KEY", "")
if skills_manager_key:
    conn.append({
        "type": "openapi",
        "url": "http://skills-manager:8000",
        "spec_type": "url",
        "spec": "",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": skills_manager_key,
        "config": {"enable": True},
        "info": {
            "id": "skills-manager",
            "name": "skills-manager",
            "description": (
                "CRUD over .ai/skills/: GET /skills, GET /skills/{id}, "
                "POST /skills, PUT /skills/{id}, DELETE /skills/{id}, "
                "POST /skills/import (aitmpl/url/claude-templates), "
                "POST /skills/{id}/attach (toggle agents list). Each "
                "write bumps the agents' AgentCard.version via "
                "/admin/reload broadcast."
            ),
        },
    })
print(json.dumps(conn))
PY
    )"

    # ── 6a. Host-side jupyter_server (нужен для CODE_EXECUTION_*/CODE_INTERPRETER_*) ──
    if [ -x "$JUPYTER_HOST_START" ] && [ -d "$SCRIPT_DIR/.venv-jupyter" ]; then
        if [ -n "${JUPYTER_TOKEN:-}" ]; then
            say "Стартую host-side jupyter_server ($JUPYTER_HOST_START)"
            "$JUPYTER_HOST_START" start || warn "jupyter-host-start.sh завершился с ошибкой"
            if jupyter_ready; then
                ok "jupyter_server /api отвечает на :$JUPYTER_PORT"
            else
                warn "jupyter_server /api не ответил — Code Interpreter в OpenWebUI не сработает"
            fi
        else
            warn "JUPYTER_TOKEN не задан — пропускаю запуск jupyter_server"
        fi
    fi

    say "Запускаю OpenWebUI ($COMPOSE_OPENWEBUI)"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_OPENWEBUI" up -d

    # ── 6b. Shellbox (CLI tool sidecar) ──────────────────────────────────────
    if [ -f "$COMPOSE_SHELLBOX" ]; then
        if [ -n "${SHELLBOX_API_KEY:-}" ]; then
            say "Запускаю shellbox ($COMPOSE_SHELLBOX)"
            SHELLBOX_API_KEY="$SHELLBOX_API_KEY" \
            SHELLBOX_HOST_PORT="$SHELLBOX_HOST_PORT" \
                docker compose --env-file "$ENV_FILE" -f "$COMPOSE_SHELLBOX" up -d --build

            say "Жду shellbox /openapi.json на :$SHELLBOX_HOST_PORT"
            SHELLBOX_OK=0
            for i in {1..45}; do
                if shellbox_ready; then
                    ok "shellbox отдаёт /openapi.json"
                    SHELLBOX_OK=1
                    break
                fi
                sleep 2
            done
            if [ "$SHELLBOX_OK" != "1" ]; then
                warn "shellbox не ответил за 90s (см. docker logs shellbox)"
            fi
        else
            warn "SHELLBOX_API_KEY не задан — пропускаю shellbox"
        fi
    fi

    # ── 6c. fsbox (FS MCP for the agent_dev sandbox) ─────────────────────────
    if [ -f "$COMPOSE_FSBOX" ]; then
        if [ -n "${FSBOX_API_KEY:-}" ]; then
            if [ ! -d "$AGENT_DEV_DIR" ]; then
                warn "$AGENT_DEV_DIR отсутствует — создаю c правами 0750"
                mkdir -p "$AGENT_DEV_DIR"
                chmod 0750 "$AGENT_DEV_DIR"
            fi
            say "Запускаю fsbox ($COMPOSE_FSBOX)"
            FSBOX_API_KEY="$FSBOX_API_KEY" \
            FSBOX_HOST_PORT="$FSBOX_HOST_PORT" \
                docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FSBOX" up -d --build

            say "Жду fsbox /openapi.json на :$FSBOX_HOST_PORT"
            FSBOX_OK=0
            for i in {1..45}; do
                if fsbox_ready; then
                    ok "fsbox отдаёт /openapi.json"
                    FSBOX_OK=1
                    break
                fi
                sleep 2
            done
            if [ "$FSBOX_OK" != "1" ]; then
                warn "fsbox не ответил за 90s (см. docker logs fsbox)"
            fi
        else
            warn "FSBOX_API_KEY не задан — пропускаю fsbox"
        fi
    fi

    # ── 6e. agent-mesh adapters (clawcode-adapter + openhands-adapter) ──────
    # Off by default until both bearer tokens are set in .env. The compose
    # file uses ${VAR:?…} so an empty key would abort docker compose; we
    # skip the whole step instead so the rest of the stack still boots.
    AGENT_MESH_ENABLED="${AGENT_MESH_ENABLED:-auto}"
    if [ -f "$COMPOSE_AGENT_MESH" ]; then
        if [ -n "${CLAWCODE_ADAPTER_API_KEY:-}" ] && [ -n "${OPENHANDS_ADAPTER_API_KEY:-}" ] \
                && { [ "$AGENT_MESH_ENABLED" = "auto" ] || [ "$AGENT_MESH_ENABLED" = "1" ]; }; then
            # `agent-registry` and `skills-manager` are required-by-default
            # services in compose.agents-mesh.yml; auto-generate dev tokens
            # if the user forgot to populate .env so `docker compose up` does
            # not abort with the `${VAR:?…}` guard.
            if [ -z "${AGENT_REGISTRY_API_KEY:-}" ]; then
                AGENT_REGISTRY_API_KEY="$(openssl rand -hex 32)"
                warn "AGENT_REGISTRY_API_KEY не задан — сгенерирован временный (rotate в .env: openssl rand -hex 32)"
                export AGENT_REGISTRY_API_KEY
            fi
            if [ -z "${SKILLS_MANAGER_API_KEY:-}" ]; then
                SKILLS_MANAGER_API_KEY="$(openssl rand -hex 32)"
                warn "SKILLS_MANAGER_API_KEY не задан — сгенерирован временный (rotate в .env: openssl rand -hex 32)"
                export SKILLS_MANAGER_API_KEY
            fi
            # Pre-create the `.ai/skills/` tree so the bind-mount has a
            # canonical mount point even on a clean checkout. The actual
            # skill payload is provisioned by the P0 bootstrap (see
            # plans/a2a-compliant_agent_mesh_*.plan.md §3.3).
            mkdir -p "$SCRIPT_DIR/.ai/skills"

            say "Запускаю agent-mesh adapters + registry + skills-manager ($COMPOSE_AGENT_MESH)"
            # opencode-adapter поднимается рядом с двумя другими — но только
            # если задан OPENCODE_ADAPTER_API_KEY. Если ключа нет, compose
            # абортнётся из-за ${VAR:?…}, поэтому либо сгенерируем dev-токен,
            # либо отключим сервис через профили (используем сценарий
            # «сгенерируй временный» для согласованности с registry/skills).
            if [ -z "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
                OPENCODE_ADAPTER_API_KEY="$(openssl rand -hex 32)"
                warn "OPENCODE_ADAPTER_API_KEY не задан — сгенерирован временный (rotate в .env: openssl rand -hex 32)"
                export OPENCODE_ADAPTER_API_KEY
            fi
            CLAWCODE_ADAPTER_API_KEY="$CLAWCODE_ADAPTER_API_KEY" \
            OPENHANDS_ADAPTER_API_KEY="$OPENHANDS_ADAPTER_API_KEY" \
            OPENCODE_ADAPTER_API_KEY="$OPENCODE_ADAPTER_API_KEY" \
            CLAWCODE_ADAPTER_HOST_PORT="${CLAWCODE_ADAPTER_HOST_PORT:-8790}" \
            OPENHANDS_ADAPTER_HOST_PORT="${OPENHANDS_ADAPTER_HOST_PORT:-8791}" \
            OPENCODE_ADAPTER_HOST_PORT="${OPENCODE_ADAPTER_HOST_PORT:-8798}" \
            CLAWCODE_ADAPTER_GRPC_HOST_PORT="${CLAWCODE_ADAPTER_GRPC_HOST_PORT:-8796}" \
            OPENHANDS_ADAPTER_GRPC_HOST_PORT="${OPENHANDS_ADAPTER_GRPC_HOST_PORT:-8797}" \
            OPENCODE_ADAPTER_GRPC_HOST_PORT="${OPENCODE_ADAPTER_GRPC_HOST_PORT:-8799}" \
            OPENCODE_ADAPTER_CONCURRENCY="${OPENCODE_ADAPTER_CONCURRENCY:-2}" \
            OPENCODE_ADAPTER_TIMEOUT="${OPENCODE_ADAPTER_TIMEOUT:-1200}" \
            OPENCODE_ADAPTER_AUTO_APPROVE="${OPENCODE_ADAPTER_AUTO_APPROVE:-workspace}" \
            MAX_NESTED_AGENT_CALLS="${MAX_NESTED_AGENT_CALLS:-1}" \
            AGENT_REGISTRY_API_KEY="$AGENT_REGISTRY_API_KEY" \
            AGENT_REGISTRY_HOST_PORT="${AGENT_REGISTRY_HOST_PORT:-8794}" \
            AGENT_REGISTRY_AGENTS="${AGENT_REGISTRY_AGENTS:-clawcode=clawcode-adapter:8790,openhands=openhands-adapter:8791,opencode=opencode-adapter:8798}" \
            SKILLS_MANAGER_API_KEY="$SKILLS_MANAGER_API_KEY" \
            SKILLS_MANAGER_HOST_PORT="${SKILLS_MANAGER_HOST_PORT:-8795}" \
            SKILLS_ADAPTERS="${SKILLS_ADAPTERS:-clawcode=http://clawcode-adapter:8790,openhands=http://openhands-adapter:8791,opencode=http://opencode-adapter:8798}" \
            A2A_BINDINGS="${A2A_BINDINGS:-rest,jsonrpc,grpc}" \
            A2A_TASK_STORE_DSN="${A2A_TASK_STORE_DSN:-memory://}" \
            A2A_PUSH_MAX_RETRIES="${A2A_PUSH_MAX_RETRIES:-5}" \
            A2A_PUSH_BACKOFF_BASE_S="${A2A_PUSH_BACKOFF_BASE_S:-1.0}" \
                docker compose --env-file "$ENV_FILE" -f "$COMPOSE_AGENT_MESH" up -d --build

            say "Жду clawcode-adapter /healthz на :${CLAWCODE_ADAPTER_HOST_PORT:-8790}"
            CC_ADAPTER_OK=0
            for i in {1..30}; do
                if clawcode_adapter_ready; then
                    ok "clawcode-adapter отвечает"
                    CC_ADAPTER_OK=1
                    break
                fi
                sleep 2
            done
            [ "$CC_ADAPTER_OK" = "1" ] || warn "clawcode-adapter не ответил за 60s (см. docker logs clawcode-adapter)"

            say "Жду openhands-adapter /healthz на :${OPENHANDS_ADAPTER_HOST_PORT:-8791}"
            OH_ADAPTER_OK=0
            for i in {1..30}; do
                if openhands_adapter_ready; then
                    ok "openhands-adapter отвечает"
                    OH_ADAPTER_OK=1
                    break
                fi
                sleep 2
            done
            [ "$OH_ADAPTER_OK" = "1" ] || warn "openhands-adapter не ответил за 60s (см. docker logs openhands-adapter)"

            say "Жду opencode-adapter /healthz на :${OPENCODE_ADAPTER_HOST_PORT:-8798}"
            OC_ADAPTER_OK=0
            for i in {1..30}; do
                if opencode_adapter_ready; then
                    ok "opencode-adapter отвечает"
                    OC_ADAPTER_OK=1
                    break
                fi
                sleep 2
            done
            [ "$OC_ADAPTER_OK" = "1" ] || warn "opencode-adapter не ответил за 60s (см. docker logs opencode-adapter — нужен ли docker exec opencode? см. opencode-start.sh)"

            say "Жду agent-registry /healthz на :${AGENT_REGISTRY_HOST_PORT:-8794}"
            AR_OK=0
            for i in {1..30}; do
                if agent_registry_ready; then
                    ok "agent-registry отвечает"
                    AR_OK=1
                    break
                fi
                sleep 2
            done
            [ "$AR_OK" = "1" ] || warn "agent-registry не ответил за 60s (см. docker logs agent-registry)"

            say "Жду skills-manager /healthz на :${SKILLS_MANAGER_HOST_PORT:-8795}"
            SM_OK=0
            for i in {1..30}; do
                if skills_manager_ready; then
                    ok "skills-manager отвечает"
                    SM_OK=1
                    break
                fi
                sleep 2
            done
            [ "$SM_OK" = "1" ] || warn "skills-manager не ответил за 60s (см. docker logs skills-manager)"
        else
            warn "agent-mesh adapters пропущены: задайте CLAWCODE_ADAPTER_API_KEY и OPENHANDS_ADAPTER_API_KEY в .env (или AGENT_MESH_ENABLED=1)"
        fi
    fi

    # ── 6d. searchbox (multi-engine MCP Search: SearXNG+Wiki+arXiv+...) ──────
    if [ -f "$COMPOSE_SEARCHBOX" ]; then
        if [ -n "${SEARCHBOX_API_KEY:-}" ]; then
            say "Запускаю searchbox ($COMPOSE_SEARCHBOX)"
            SEARCHBOX_API_KEY="$SEARCHBOX_API_KEY" \
            SEARCHBOX_HOST_PORT="$SEARCHBOX_HOST_PORT" \
            SEARCHBOX_MCP_HOST_PORT="$SEARCHBOX_MCP_HOST_PORT" \
            SEARCHBOX_SEARXNG_URL="${SEARCHBOX_SEARXNG_URL:-http://searxng:8080}" \
            SEARCHBOX_HTTP_TIMEOUT="${SEARCHBOX_HTTP_TIMEOUT:-10}" \
            SEARCHBOX_LOG_LEVEL="${SEARCHBOX_LOG_LEVEL:-INFO}" \
            BRAVE_API_KEY="${BRAVE_API_KEY:-}" \
            GOOGLE_API_KEY="${GOOGLE_API_KEY:-}" \
            GOOGLE_CX="${GOOGLE_CX:-}" \
            GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
            TAVILY_API_KEY="${TAVILY_API_KEY:-}" \
            STACKEXCHANGE_KEY="${STACKEXCHANGE_KEY:-}" \
            OPENALEX_MAILTO="${OPENALEX_MAILTO:-}" \
                docker compose --env-file "$ENV_FILE" -f "$COMPOSE_SEARCHBOX" up -d --build

            say "Жду searchbox /openapi.json на :$SEARCHBOX_HOST_PORT"
            SEARCHBOX_OK=0
            for i in {1..45}; do
                if searchbox_ready; then
                    ok "searchbox отдаёт /openapi.json"
                    SEARCHBOX_OK=1
                    break
                fi
                sleep 2
            done
            if [ "$SEARCHBOX_OK" != "1" ]; then
                warn "searchbox не ответил за 90s (см. docker logs searchbox)"
            fi
        else
            warn "SEARCHBOX_API_KEY не задан — пропускаю searchbox"
        fi
    fi

    say "Жду OpenWebUI на :$OPENWEBUI_HOST_PORT"
    for i in {1..60}; do
        if openwebui_ready; then
            ok "OpenWebUI отвечает"
            break
        fi
        sleep 2
        [ "$i" = 60 ] && die "OpenWebUI не поднялся за 120s (см. docker logs open-webui)"
    done
else
    warn "OPENWEBUI_ENABLED=0 — пропускаю запуск OpenWebUI"
fi

echo
echo "=========================================="
echo " Phoenix UI:      http://localhost:$PHOENIX_HOST_PORT"
echo " LiteLLM:         http://localhost:$LITELLM_HOST_PORT/v1  (key: $LITELLM_API_KEY)"
echo " LiteLLM UI:      http://localhost:$LITELLM_HOST_PORT/ui"
echo " LiteLLM login:   $LITELLM_UI_USERNAME / $LITELLM_UI_PASSWORD"
case "$LLM_BACKEND" in
    llama)
        echo " llama-server:    http://localhost:$LLAMA_HOST_PORT/v1 (Docker)"
        ;;
    lmstudio)
        echo " LM Studio:       http://localhost:$LMSTUDIO_HOST_PORT/v1"
        ;;
    llamacpp)
        echo " llama.cpp host:  http://localhost:$LLAMA_CPP_HOST_PORT/v1"
        ;;
    both)
        echo " LM Studio:       http://localhost:$LMSTUDIO_HOST_PORT/v1"
        echo " llama.cpp host:  http://localhost:$LLAMA_CPP_HOST_PORT/v1  (alias qwen3.6-35b-heretic)"
        ;;
esac
echo " LocalAI:         http://localhost:$LOCALAI_HOST_PORT"
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    echo " LocalAI legacy:  qwen3.6-35b-heretic (ctx 262144)"
fi
if [ -n "${CLAWCODE_ADAPTER_API_KEY:-}" ] && [ -n "${OPENHANDS_ADAPTER_API_KEY:-}" ]; then
    echo " clawcode-adapter:  http://127.0.0.1:${CLAWCODE_ADAPTER_HOST_PORT:-8790}/openapi.json  (bearer: CLAWCODE_ADAPTER_API_KEY)"
    echo " clawcode A2A REST: http://127.0.0.1:${CLAWCODE_ADAPTER_HOST_PORT:-8790}/a2a/v1  (+ /.well-known/agent-card.json, /a2a/jsonrpc, :${CLAWCODE_ADAPTER_GRPC_HOST_PORT:-8796} gRPC)"
    echo " openhands-adapter: http://127.0.0.1:${OPENHANDS_ADAPTER_HOST_PORT:-8791}/openapi.json  (bearer: OPENHANDS_ADAPTER_API_KEY)"
    echo " openhands A2A REST: http://127.0.0.1:${OPENHANDS_ADAPTER_HOST_PORT:-8791}/a2a/v1  (+ /.well-known/agent-card.json, /a2a/jsonrpc, :${OPENHANDS_ADAPTER_GRPC_HOST_PORT:-8797} gRPC)"
fi
if [ -n "${OPENCODE_ADAPTER_API_KEY:-}" ]; then
    echo " opencode-adapter:  http://127.0.0.1:${OPENCODE_ADAPTER_HOST_PORT:-8798}/openapi.json  (bearer: OPENCODE_ADAPTER_API_KEY)"
    echo " opencode A2A REST: http://127.0.0.1:${OPENCODE_ADAPTER_HOST_PORT:-8798}/a2a/v1  (+ /.well-known/agent-card.json, /a2a/jsonrpc, :${OPENCODE_ADAPTER_GRPC_HOST_PORT:-8799} gRPC)"
    echo "                    ACP bridge requires `docker exec opencode opencode acp` — bring up: bash opencode-start.sh --no-attach"
fi
if [ -n "${AGENT_REGISTRY_API_KEY:-}" ]; then
    echo " agent-registry:    http://127.0.0.1:${AGENT_REGISTRY_HOST_PORT:-8794}/openapi.json  (bearer: AGENT_REGISTRY_API_KEY)"
    echo "                    GET /v1/agents, GET /v1/tasks?agents=*, POST /v1/agents/{id}/message:send|stream"
fi
if [ -n "${SKILLS_MANAGER_API_KEY:-}" ]; then
    echo " skills-manager:    http://127.0.0.1:${SKILLS_MANAGER_HOST_PORT:-8795}/openapi.json  (bearer: SKILLS_MANAGER_API_KEY)"
    echo "                    GET/POST/PUT/DELETE /skills[/{id}], POST /skills/import, POST /skills/{id}/attach"
fi
if [ "$OPENWEBUI_ENABLED" = "1" ]; then
    echo " OpenWebUI:       http://localhost:$OPENWEBUI_HOST_PORT"
    echo " OpenWebUI lang:  $OPENWEBUI_DEFAULT_LOCALE"
    if [ -n "${OPENWEBUI_ADMIN_EMAIL:-}" ]; then
        echo " OpenWebUI admin: $OPENWEBUI_ADMIN_EMAIL"
    else
        echo " OpenWebUI admin: first sign-up via UI (or set OPENWEBUI_ADMIN_EMAIL/PASSWORD)"
    fi
fi
echo
echo " Smoke-tests:     bash $SCRIPT_DIR/stack-smoke.sh"
echo "=========================================="
