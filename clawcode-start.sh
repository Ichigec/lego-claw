#!/usr/bin/env bash
# Launcher for Claw Code (https://claw-code.codes/).
#
# Mirrors openhands-start.sh layout: pre-flights for Docker, llm-stack-net,
# LiteLLM auth, default model availability, then `docker compose up -d`.
# Final touch is `docker exec -it clawcode claw --model <alias>` (Rust CLI) which
# drops the user into an interactive CLI session against the running
# container. The sidecar itself stays alive (`tail -f /dev/null`) so a
# second exec keeps working without restarting the container.
#
# Run from the project root:
#   bash clawcode-start.sh              # build (if needed) + start + interactive CLI
#   bash clawcode-start.sh --no-attach  # bring container up but skip exec
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.clawcode}"
COMPOSE_FILE="$SCRIPT_DIR/compose.clawcode.yml"

ATTACH=1
for arg in "$@"; do
    case "$arg" in
        --no-attach) ATTACH=0 ;;
        -h|--help)
            echo "Usage: bash $0 [--no-attach]"
            echo "  --no-attach  поднять контейнер и выйти без интерактивного docker exec"
            exit 0
            ;;
    esac
done

_CC_ENV_KEYS=(
    CC_COMPOSE_PROJECT
    CLAWCODE_HOST_PORT
    CLAWCODE_IMAGE
    CLAWCODE_GIT_REPO
    CLAWCODE_GIT_REF
    CLAWCODE_DEFAULT_MODEL
    CLAWCODE_WORKSPACE_DIR
    CLAWCODE_STATE_DIR
    CLAWCODE_LITELLM_BASE_URL
    CLAWCODE_LITELLM_API_KEY
    CLAWCODE_USER_UID
    CLAWCODE_USER_GID
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

load_selected_env "$ENV_FILE" "${_CC_ENV_KEYS[@]}"
load_selected_env "$ENV_OVERRIDE_FILE" "${_CC_ENV_KEYS[@]}"

_cc_expand_home() {
    local v="$1"
    case "$v" in
        '${HOME}'*) printf '%s' "${EFFECTIVE_HOME:-$HOME}${v#\$\{HOME\}}" ;;
        '$HOME'*)   printf '%s' "${EFFECTIVE_HOME:-$HOME}${v#\$HOME}" ;;
        '~/'*)      printf '%s' "${EFFECTIVE_HOME:-$HOME}/${v#\~/}" ;;
        *)          printf '%s' "$v" ;;
    esac
}

if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    EFFECTIVE_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
elif [ "$(id -u)" = "0" ]; then
    _repo_owner="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || echo "")"
    if [ -n "$_repo_owner" ] && [ "$_repo_owner" != "root" ]; then
        EFFECTIVE_HOME="$(getent passwd "$_repo_owner" | cut -d: -f6)"
    fi
fi
EFFECTIVE_HOME="${EFFECTIVE_HOME:-$HOME}"

CC_COMPOSE_PROJECT="${CC_COMPOSE_PROJECT:-clawcode}"
CLAWCODE_DEFAULT_MODEL="${CLAWCODE_DEFAULT_MODEL:-openai/qwen3.6-35b-heretic}"
CLAWCODE_WORKSPACE_DIR="$(_cc_expand_home "${CLAWCODE_WORKSPACE_DIR:-$EFFECTIVE_HOME/agent_dev}")"
CLAWCODE_STATE_DIR="$(_cc_expand_home "${CLAWCODE_STATE_DIR:-$EFFECTIVE_HOME/.clawcode}")"
CLAWCODE_LITELLM_BASE_URL="${CLAWCODE_LITELLM_BASE_URL:-http://litellm:4000/v1}"
CLAWCODE_USER_UID="${CLAWCODE_USER_UID:-10101}"
CLAWCODE_USER_GID="${CLAWCODE_USER_GID:-10101}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
CLAWCODE_LITELLM_API_KEY="${CLAWCODE_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export CLAWCODE_WORKSPACE_DIR CLAWCODE_STATE_DIR CLAWCODE_LITELLM_API_KEY \
       CLAWCODE_USER_UID CLAWCODE_USER_GID

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

echo "=== Claw Code — пробный запуск ==="
echo "→ Workspace:    $CLAWCODE_WORKSPACE_DIR"
echo "→ State dir:    $CLAWCODE_STATE_DIR"
echo "→ Default LLM:  $CLAWCODE_DEFAULT_MODEL"
echo "→ LiteLLM URL:  $CLAWCODE_LITELLM_BASE_URL"
echo "→ Git ref:      ${CLAWCODE_GIT_REF:-main} (CLAWCODE_GIT_REPO=${CLAWCODE_GIT_REPO:-https://github.com/instructkr/claw-code.git})"

# ── 1. Docker ──────────────────────────────────────────────────────────────
if ! docker info >/dev/null 2>&1; then
    cat >&2 <<EOF
Docker недоступен из текущей сессии.
Если пользователь ещё не в группе docker:
  sudo usermod -aG docker "${USER:-$LOGNAME}" && newgrp docker
EOF
    exit 1
fi

# ── 2. compose-сеть llm-stack-net ──────────────────────────────────────────
if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    die "Сеть llm-stack-net не найдена. Сначала: bash \"$SCRIPT_DIR/stack-start.sh\""
fi

# ── 3. LiteLLM авторизует наш ключ ─────────────────────────────────────────
LITELLM_PROBE_URL="http://localhost:$LITELLM_HOST_PORT/v1/models"
if ! curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" "$LITELLM_PROBE_URL" >/dev/null 2>&1; then
    die "LiteLLM на $LITELLM_PROBE_URL не отвечает или не принимает ключ. Запустите: bash $SCRIPT_DIR/stack-start.sh"
fi

# ── 4. Алиас модели присутствует ───────────────────────────────────────────
if ! curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" "$LITELLM_PROBE_URL" \
        | grep -q "\"id\":\"$CLAWCODE_DEFAULT_MODEL\""; then
    warn "В LiteLLM нет alias '$CLAWCODE_DEFAULT_MODEL' — Claw Code стартует, но запросы упадут 404 пока вы не выберете другую модель."
fi

# ── 5. Workspace + state директории ────────────────────────────────────────
mkdir -p "$CLAWCODE_WORKSPACE_DIR"
mkdir -p "$CLAWCODE_STATE_DIR"

# ── 5.5 ACL — uid контейнера = CLAWCODE_USER_UID; даём ему rwx ────────────
if command -v setfacl >/dev/null 2>&1; then
    if setfacl -R \
            -m "u:${CLAWCODE_USER_UID}:rwx,g:${CLAWCODE_USER_GID}:rwx" \
            "$CLAWCODE_WORKSPACE_DIR" 2>/dev/null \
       && setfacl -R -d \
            -m "u:${CLAWCODE_USER_UID}:rwx,g:${CLAWCODE_USER_GID}:rwx,u:1000:rwx,g:1000:rwx" \
            "$CLAWCODE_WORKSPACE_DIR" 2>/dev/null; then
        ok "ACL применён к $CLAWCODE_WORKSPACE_DIR (uid=${CLAWCODE_USER_UID})"
    else
        warn "setfacl не сработал — Claw Code может не записать в $CLAWCODE_WORKSPACE_DIR"
    fi
    chown -R "${CLAWCODE_USER_UID}:${CLAWCODE_USER_GID}" "$CLAWCODE_STATE_DIR" 2>/dev/null \
        || sudo -n chown -R "${CLAWCODE_USER_UID}:${CLAWCODE_USER_GID}" "$CLAWCODE_STATE_DIR" 2>/dev/null \
        || warn "не получилось установить владельца на $CLAWCODE_STATE_DIR (попробуйте вручную: sudo chown -R ${CLAWCODE_USER_UID}:${CLAWCODE_USER_GID} $CLAWCODE_STATE_DIR)"
else
    warn "setfacl не установлен (sudo apt-get install -y acl)."
fi

# ── 6. Build + start ───────────────────────────────────────────────────────
echo ""
echo "→ docker compose -p ${CC_COMPOSE_PROJECT} up -d --build (ref ${CLAWCODE_GIT_REF:-main})"
docker compose -p "$CC_COMPOSE_PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

# ── 7. Wait for the container to settle ────────────────────────────────────
for i in {1..30}; do
    state="$(docker inspect --format='{{.State.Status}}' clawcode 2>/dev/null || echo "missing")"
    if [ "$state" = "running" ]; then
        ok "clawcode-контейнер запущен (state=running)"
        break
    fi
    sleep 1
    [ "$i" = 30 ] && die "Контейнер clawcode не стал running за 30s — см. docker logs clawcode"
done

# ── 8. Summary + optional interactive attach ───────────────────────────────
cat <<EOF

==========================================
 Claw Code container: docker exec -it clawcode bash
 Через LiteLLM:       $CLAWCODE_LITELLM_BASE_URL
 Default model:       $CLAWCODE_DEFAULT_MODEL
 Workspace:           $CLAWCODE_WORKSPACE_DIR (внутри: /workspace/project)
 State:               $CLAWCODE_STATE_DIR (внутри: /.clawcode)
 MCP servers:         ${CLAWCODE_MCP_SERVERS:-[]}

 Остановить:          bash $SCRIPT_DIR/clawcode-stop.sh
 Smoke (headless):    bash $SCRIPT_DIR/clawcode-demo-ru.sh
==========================================
EOF

if [ "$ATTACH" = "1" ]; then
    echo ""
    echo "→ Открываю интерактивную CLI-сессию (Ctrl-D / exit для выхода)…"
    exec docker exec -it clawcode claw --model "${CLAWCODE_DEFAULT_MODEL}"
fi
