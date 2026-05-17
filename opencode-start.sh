#!/usr/bin/env bash
# Launcher for opencode (https://opencode.ai/).
#
# Mirrors clawcode-start.sh layout: pre-flights for Docker, llm-stack-net,
# LiteLLM auth, default model availability, then `docker compose up -d`.
# Final touch is `docker exec -it opencode opencode` (TUI) which drops the
# user into an interactive session inside the running container.
# The sidecar stays alive (`tail -f /dev/null`) so a second
# `docker exec` keeps working without restarting the container — in
# particular `docker exec -i opencode opencode acp` from opencode-adapter
# reuses the same process.
#
# Run from the project root:
#   bash opencode-start.sh              # build (if needed) + start + interactive TUI
#   bash opencode-start.sh --no-attach  # bring container up but skip exec
#   bash opencode-start.sh --web        # bring container up + start web UI on :3400
#                                       # (implies --no-attach; URL printed at the end)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.opencode}"
COMPOSE_FILE="$SCRIPT_DIR/compose.opencode.yml"

ATTACH=1
WEB=0
for arg in "$@"; do
    case "$arg" in
        --no-attach) ATTACH=0 ;;
        --web) WEB=1; ATTACH=0 ;;
        -h|--help)
            cat <<USAGE
Usage: bash $0 [--no-attach | --web]
  (no args)    build (if needed) + start + drop into interactive TUI
  --no-attach  bring container up but skip exec (good for CI / agent-mesh)
  --web        bring container up + launch 'opencode web' UI in background
               on http://127.0.0.1:\${OPENCODE_WEB_HOST_PORT:-3400}
               (implies --no-attach; equivalent to
                opencode-start.sh --no-attach && opencode-web-start.sh)
USAGE
            exit 0
            ;;
    esac
done

_OC_ENV_KEYS=(
    OPENCODE_COMPOSE_PROJECT
    OPENCODE_IMAGE
    OPENCODE_VERSION
    OPENCODE_NODE_VERSION
    OPENCODE_DEFAULT_MODEL
    OPENCODE_WORKSPACE_DIR
    OPENCODE_STATE_DIR
    OPENCODE_LITELLM_BASE_URL
    OPENCODE_LITELLM_API_KEY
    OPENCODE_USER_UID
    OPENCODE_USER_GID
    OPENCODE_MCP_SERVERS
    OPENCODE_EXTRA_LSP
    OPENCODE_WEB_HOST_PORT
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

load_selected_env "$ENV_FILE" "${_OC_ENV_KEYS[@]}"
load_selected_env "$ENV_OVERRIDE_FILE" "${_OC_ENV_KEYS[@]}"

_oc_expand_home() {
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

OPENCODE_COMPOSE_PROJECT="${OPENCODE_COMPOSE_PROJECT:-opencode}"
OPENCODE_DEFAULT_MODEL="${OPENCODE_DEFAULT_MODEL:-litellm/qwen3.6-35b-heretic}"
OPENCODE_WORKSPACE_DIR="$(_oc_expand_home "${OPENCODE_WORKSPACE_DIR:-$EFFECTIVE_HOME/agent_dev}")"
OPENCODE_STATE_DIR="$(_oc_expand_home "${OPENCODE_STATE_DIR:-$EFFECTIVE_HOME/.opencode}")"
OPENCODE_LITELLM_BASE_URL="${OPENCODE_LITELLM_BASE_URL:-http://litellm:4000/v1}"
OPENCODE_USER_UID="${OPENCODE_USER_UID:-10102}"
OPENCODE_USER_GID="${OPENCODE_USER_GID:-10102}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
OPENCODE_LITELLM_API_KEY="${OPENCODE_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export OPENCODE_WORKSPACE_DIR OPENCODE_STATE_DIR OPENCODE_LITELLM_API_KEY \
       OPENCODE_USER_UID OPENCODE_USER_GID

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

# `provider/model` (LiteLLM) — это то, что smoke ниже спросит у LiteLLM.
# opencode'у мы отдадим `OPENCODE_DEFAULT_MODEL` (вариант `litellm/<id>`).
LITELLM_DEFAULT_MODEL="${OPENCODE_DEFAULT_MODEL#litellm/}"
case "$LITELLM_DEFAULT_MODEL" in
    */*) ;;  # уже provider/model
    *)   LITELLM_DEFAULT_MODEL="openai/$LITELLM_DEFAULT_MODEL" ;;
esac

echo "=== opencode — пробный запуск ==="
echo "→ Workspace:    $OPENCODE_WORKSPACE_DIR"
echo "→ State dir:    $OPENCODE_STATE_DIR"
echo "→ Default LLM:  $OPENCODE_DEFAULT_MODEL (LiteLLM probe: $LITELLM_DEFAULT_MODEL)"
echo "→ LiteLLM URL:  $OPENCODE_LITELLM_BASE_URL"
echo "→ Version:      ${OPENCODE_VERSION:-latest}"

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
        | grep -q "\"id\":\"$LITELLM_DEFAULT_MODEL\""; then
    warn "В LiteLLM нет alias '$LITELLM_DEFAULT_MODEL' — opencode стартует, но запросы упадут 404 пока вы не выберете другую модель."
fi

# ── 5. Workspace + state директории ────────────────────────────────────────
mkdir -p "$OPENCODE_WORKSPACE_DIR"
mkdir -p "$OPENCODE_STATE_DIR"

# ── 5.5 ACL — uid контейнера = OPENCODE_USER_UID; даём ему rwx ────────────
if command -v setfacl >/dev/null 2>&1; then
    if setfacl -R \
            -m "u:${OPENCODE_USER_UID}:rwx,g:${OPENCODE_USER_GID}:rwx" \
            "$OPENCODE_WORKSPACE_DIR" 2>/dev/null \
       && setfacl -R -d \
            -m "u:${OPENCODE_USER_UID}:rwx,g:${OPENCODE_USER_GID}:rwx,u:1000:rwx,g:1000:rwx" \
            "$OPENCODE_WORKSPACE_DIR" 2>/dev/null; then
        ok "ACL применён к $OPENCODE_WORKSPACE_DIR (uid=${OPENCODE_USER_UID})"
    else
        warn "setfacl не сработал — opencode может не записать в $OPENCODE_WORKSPACE_DIR"
    fi
    chown -R "${OPENCODE_USER_UID}:${OPENCODE_USER_GID}" "$OPENCODE_STATE_DIR" 2>/dev/null \
        || sudo -n chown -R "${OPENCODE_USER_UID}:${OPENCODE_USER_GID}" "$OPENCODE_STATE_DIR" 2>/dev/null \
        || warn "не получилось установить владельца на $OPENCODE_STATE_DIR (попробуйте вручную: sudo chown -R ${OPENCODE_USER_UID}:${OPENCODE_USER_GID} $OPENCODE_STATE_DIR)"
else
    warn "setfacl не установлен (sudo apt-get install -y acl)."
fi

# ── 6. Build + start ───────────────────────────────────────────────────────
echo ""
echo "→ docker compose -p ${OPENCODE_COMPOSE_PROJECT} up -d --build (version ${OPENCODE_VERSION:-latest})"
docker compose -p "$OPENCODE_COMPOSE_PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

# ── 7. Wait for the container to settle ────────────────────────────────────
for i in {1..30}; do
    state="$(docker inspect --format='{{.State.Status}}' opencode 2>/dev/null || echo "missing")"
    if [ "$state" = "running" ]; then
        ok "opencode-контейнер запущен (state=running)"
        break
    fi
    sleep 1
    [ "$i" = 30 ] && die "Контейнер opencode не стал running за 30s — см. docker logs opencode"
done

# ── 8. Summary + optional interactive attach ───────────────────────────────
cat <<EOF

==========================================
 opencode container:  docker exec -it opencode bash
 ACP bridge:          docker exec -i opencode opencode acp   (used by opencode-adapter)
 Через LiteLLM:       $OPENCODE_LITELLM_BASE_URL
 Default model:       $OPENCODE_DEFAULT_MODEL
 Workspace:           $OPENCODE_WORKSPACE_DIR (внутри: /workspace/project)
 State:               $OPENCODE_STATE_DIR (внутри: /.opencode)
 MCP servers:         ${OPENCODE_MCP_SERVERS:-[]}

 Опц. web-UI:         bash $SCRIPT_DIR/opencode-web-start.sh
 Остановить:          bash $SCRIPT_DIR/opencode-stop.sh
 Smoke (headless):    bash $SCRIPT_DIR/opencode-demo-ru.sh
==========================================
EOF

if [ "$WEB" = "1" ]; then
    echo ""
    echo "→ Запускаю opencode web UI (на хосте: http://127.0.0.1:${OPENCODE_WEB_HOST_PORT:-3400})…"
    exec bash "$SCRIPT_DIR/opencode-web-start.sh"
fi

if [ "$ATTACH" = "1" ]; then
    echo ""
    echo "→ Открываю интерактивную TUI-сессию (Ctrl-D / :q для выхода)…"
    exec docker exec -it opencode opencode
fi
