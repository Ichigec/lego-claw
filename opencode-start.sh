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
#   bash opencode-start.sh --no-attach  # bring container up + start web UI on :3400
#                                       # (skip TUI; web URL printed at the end)
#   bash opencode-start.sh --web        # alias of --no-attach (kept for compatibility)
#   bash opencode-start.sh --no-web     # container only, no TUI, no web (CI / agent-mesh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.opencode}"
COMPOSE_FILE="$SCRIPT_DIR/compose.opencode.yml"

ATTACH=1
WEB=auto   # auto = on whenever ATTACH=0; force on with --web; off with --no-web
for arg in "$@"; do
    case "$arg" in
        --no-attach) ATTACH=0 ;;
        --web)       WEB=1; ATTACH=0 ;;
        --no-web)    WEB=0; ATTACH=0 ;;
        -h|--help)
            cat <<USAGE
Usage: bash $0 [--no-attach | --web | --no-web]
  (no args)    build (if needed) + start + drop into interactive TUI
  --no-attach  bring container up + start web UI on
               http://127.0.0.1:\${OPENCODE_WEB_HOST_PORT:-3400} in background
               (no TUI; equivalent to opencode-start.sh --web)
  --web        alias of --no-attach (kept for backwards compat)
  --no-web     bring container up only — no TUI, no web UI
               (CI / agent-mesh: nobody on the host needs the browser UI,
                opencode-adapter still talks to the container via 'docker
                exec opencode opencode acp')
USAGE
            exit 0
            ;;
    esac
done

# Resolve auto: --no-attach implies web by default. Explicit --no-web wins.
if [ "$WEB" = "auto" ]; then
    if [ "$ATTACH" = "0" ]; then WEB=1; else WEB=0; fi
fi

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
# Workspace и state-dir bind-mount'ятся как есть, поэтому inside-container
# uid 10102 должен иметь rwx на оба. setfacl работает без root (хост-юзер
# уже владеет директорией). chown нам недоступен из-под обычного юзера —
# не пытаемся (раньше падали на sudo -n).
if command -v setfacl >/dev/null 2>&1; then
    _oc_acl_target() {
        local dir="$1"
        setfacl -R \
            -m "u:${OPENCODE_USER_UID}:rwx,g:${OPENCODE_USER_GID}:rwx" \
            "$dir" 2>/dev/null \
        && setfacl -R -d \
            -m "u:${OPENCODE_USER_UID}:rwx,g:${OPENCODE_USER_GID}:rwx,u:1000:rwx,g:1000:rwx" \
            "$dir" 2>/dev/null
    }
    if _oc_acl_target "$OPENCODE_WORKSPACE_DIR"; then
        ok "ACL применён к $OPENCODE_WORKSPACE_DIR (uid=${OPENCODE_USER_UID})"
    else
        warn "setfacl не сработал на $OPENCODE_WORKSPACE_DIR — opencode может не записать туда"
    fi
    if _oc_acl_target "$OPENCODE_STATE_DIR"; then
        ok "ACL применён к $OPENCODE_STATE_DIR (uid=${OPENCODE_USER_UID})"
    else
        warn "setfacl не сработал на $OPENCODE_STATE_DIR — opencode не сможет писать /.opencode/web.log и т.п."
    fi
else
    warn "setfacl не установлен (sudo apt-get install -y acl). Без ACL opencode (uid ${OPENCODE_USER_UID}) не сможет писать в $OPENCODE_STATE_DIR."
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

# ── 8. Web UI (по умолчанию для --no-attach) ──────────────────────────────
if [ "$WEB" = "1" ]; then
    echo ""
    echo "→ Поднимаю opencode web UI (на хосте: http://127.0.0.1:${OPENCODE_WEB_HOST_PORT:-3400})…"
    if ! bash "$SCRIPT_DIR/opencode-web-start.sh"; then
        warn "opencode web UI не запустился — см. docker exec opencode tail -f /.opencode/web.log"
        warn "Контейнер живой, ACP-bridge для opencode-adapter работает; повторить вручную: bash $SCRIPT_DIR/opencode-web-start.sh --restart"
    fi
fi

# ── 9. Summary + optional interactive attach ──────────────────────────────
cat <<EOF

==========================================
 opencode container:  docker exec -it opencode bash
 ACP bridge:          docker exec -i opencode opencode acp   (used by opencode-adapter)
 Через LiteLLM:       $OPENCODE_LITELLM_BASE_URL
 Default model:       $OPENCODE_DEFAULT_MODEL
 Workspace:           $OPENCODE_WORKSPACE_DIR (внутри: /workspace/project)
 State:               $OPENCODE_STATE_DIR (внутри: /.opencode)
 MCP servers:         ${OPENCODE_MCP_SERVERS:-[]}
EOF

if [ "$WEB" = "1" ]; then
    cat <<EOF
 Web UI:              http://127.0.0.1:${OPENCODE_WEB_HOST_PORT:-3400}
                      (логи: docker exec opencode tail -f /.opencode/web.log)
EOF
else
    cat <<EOF
 Web UI:              отключён (--no-web). Поднять отдельно: bash $SCRIPT_DIR/opencode-web-start.sh
EOF
fi

cat <<EOF
 Остановить:          bash $SCRIPT_DIR/opencode-stop.sh
 Smoke (headless):    bash $SCRIPT_DIR/opencode-demo-ru.sh
==========================================
EOF

if [ "$ATTACH" = "1" ]; then
    echo ""
    echo "→ Открываю интерактивную TUI-сессию (Ctrl-D / :q для выхода)…"
    exec docker exec -it opencode opencode
fi
