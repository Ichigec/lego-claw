#!/usr/bin/env bash
# Trial launch script for OpenHands (https://github.com/All-Hands-AI/OpenHands).
#
# Why a separate launcher (and not part of stack-start.sh)?
#   OpenHands is a heavy combine: the app image is ~2 GB and it lazy-pulls
#   a runtime sandbox image (~2-3 GB) on first session, plus mounts
#   docker.sock and runs nested containers. Most stack work (UI/voice/RAG)
#   does not need it, so we keep it on a separate switch.
#
# Run from the project root: bash openhands-start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.openhands}"
COMPOSE_FILE="$SCRIPT_DIR/compose.openhands.yml"

_OH_ENV_KEYS=(
    OPENHANDS_HOST_PORT
    OPENHANDS_IMAGE
    OPENHANDS_AGENT_SERVER_REPO
    OPENHANDS_AGENT_SERVER_TAG
    OPENHANDS_DEFAULT_MODEL
    OPENHANDS_WORKSPACE_DIR
    OPENHANDS_STATE_DIR
    OPENHANDS_LITELLM_BASE_URL
    OPENHANDS_LITELLM_API_KEY
    OPENHANDS_LOG_ALL_EVENTS
    OPENHANDS_MCP_SERVERS
    LITELLM_HOST_PORT
    LITELLM_API_KEY
)

# Parser shared with llamacpp-host-start.sh: only export the keys we care
# about, and don't choke on lines that look like shell redirection (e.g.
# `OPENWEBUI_SEARXNG_QUERY_URL=http://...?q=<query>&format=json`).
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

load_selected_env "$ENV_FILE" "${_OH_ENV_KEYS[@]}"
load_selected_env "$ENV_OVERRIDE_FILE" "${_OH_ENV_KEYS[@]}"

# load_selected_env stores values verbatim — it does NOT perform shell
# expansion. If someone wrote `OPENHANDS_STATE_DIR=${HOME}/.openhands` in
# .env.openhands, we'd end up with a literal `${HOME}/…` which docker compose
# interprets as a named volume reference and rejects. Expand $HOME / ${HOME}
# at the head of path-like variables so older env files keep working.
_oh_expand_home() {
    local v="$1"
    case "$v" in
        '${HOME}'*) printf '%s' "${EFFECTIVE_HOME:-$HOME}${v#\$\{HOME\}}" ;;
        '$HOME'*)   printf '%s' "${EFFECTIVE_HOME:-$HOME}${v#\$HOME}" ;;
        '~/'*)      printf '%s' "${EFFECTIVE_HOME:-$HOME}/${v#\~/}" ;;
        *)          printf '%s' "$v" ;;
    esac
}

# Pick the home we actually want to use for state. When the launcher is run
# as root (e.g. because the invoking user is not in the docker group), $HOME
# resolves to /root — but the repo belongs to a regular user and that's where
# persistent OpenHands state should live. Prefer SUDO_USER, then the repo
# owner, then fall back to $HOME.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    EFFECTIVE_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
elif [ "$(id -u)" = "0" ]; then
    _repo_owner="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || echo "")"
    if [ -n "$_repo_owner" ] && [ "$_repo_owner" != "root" ]; then
        EFFECTIVE_HOME="$(getent passwd "$_repo_owner" | cut -d: -f6)"
    fi
fi
EFFECTIVE_HOME="${EFFECTIVE_HOME:-$HOME}"

OPENHANDS_HOST_PORT="${OPENHANDS_HOST_PORT:-3300}"
OPENHANDS_DEFAULT_MODEL="${OPENHANDS_DEFAULT_MODEL:-qwen3.6-35b-heretic}"
OPENHANDS_WORKSPACE_DIR="$(_oh_expand_home "${OPENHANDS_WORKSPACE_DIR:-$EFFECTIVE_HOME/agent_dev}")"
OPENHANDS_STATE_DIR="$(_oh_expand_home "${OPENHANDS_STATE_DIR:-$EFFECTIVE_HOME/.openhands}")"
OPENHANDS_LITELLM_BASE_URL="${OPENHANDS_LITELLM_BASE_URL:-http://litellm:4000/v1}"
LITELLM_HOST_PORT="${LITELLM_HOST_PORT:-4000}"
LITELLM_API_KEY="${LITELLM_API_KEY:-sk-local}"
# OPENHANDS_LITELLM_API_KEY falls back to LITELLM_API_KEY when empty.
OPENHANDS_LITELLM_API_KEY="${OPENHANDS_LITELLM_API_KEY:-$LITELLM_API_KEY}"
export OPENHANDS_WORKSPACE_DIR OPENHANDS_STATE_DIR OPENHANDS_LITELLM_API_KEY

echo "=== OpenHands — пробный запуск ==="
echo "→ Host port:   $OPENHANDS_HOST_PORT"
echo "→ Workspace:   $OPENHANDS_WORKSPACE_DIR"
echo "→ State dir:   $OPENHANDS_STATE_DIR"
echo "→ Default LLM: litellm_proxy/$OPENHANDS_DEFAULT_MODEL"
echo "→ LiteLLM URL: $OPENHANDS_LITELLM_BASE_URL"

# ── 1. Docker reachable from the current shell ─────────────────────────────
if ! docker info >/dev/null 2>&1; then
    cat >&2 <<EOF
Docker недоступен из текущей сессии.
Если пользователь ещё не в группе docker:
  sudo usermod -aG docker "${USER:-$LOGNAME}" && newgrp docker
EOF
    exit 1
fi

# ── 2. Compose network (created by stack-start.sh) ─────────────────────────
if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    cat >&2 <<EOF
Сеть llm-stack-net не найдена.
Сначала подними основной стек:
  bash "$SCRIPT_DIR/stack-start.sh"
EOF
    exit 1
fi

# ── 3. LiteLLM is up and authorising us ────────────────────────────────────
LITELLM_PROBE_URL="http://localhost:$LITELLM_HOST_PORT/v1/models"
if ! curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" "$LITELLM_PROBE_URL" >/dev/null 2>&1; then
    cat >&2 <<EOF
LiteLLM на $LITELLM_PROBE_URL сейчас не отвечает (или не принимает ключ).
Сначала подними основной стек:
  bash "$SCRIPT_DIR/stack-start.sh"
EOF
    exit 1
fi

# ── 4. Verify the default model is registered in LiteLLM ───────────────────
if ! curl -fsS -m 5 -H "Authorization: Bearer $LITELLM_API_KEY" "$LITELLM_PROBE_URL" \
        | grep -q "\"id\":\"$OPENHANDS_DEFAULT_MODEL\""; then
    echo "⚠  В LiteLLM нет алиаса '$OPENHANDS_DEFAULT_MODEL' (см. docker/litellm/config.yaml)."
    echo "    OpenHands всё равно стартует, но запросы к этому ID будут падать 404,"
    echo "    пока вы не выберете другую модель в Settings → LLM."
fi

# ── 5. Workspace + state dirs exist on host ────────────────────────────────
mkdir -p "$OPENHANDS_WORKSPACE_DIR"
mkdir -p "$OPENHANDS_STATE_DIR"

# ── 5.1. Pre-seed settings.json so the UI doesn't show the API-key modal ───
# OpenHands shows a "configure your LLM" onboarding modal whenever
# ~/.openhands/settings.json is missing — even when the env vars in
# compose.openhands.yml already point at our LiteLLM. Seed a minimal file
# the first time so `bash openhands-start.sh` lands the user straight in
# a usable chat. We never overwrite an existing settings.json: that file
# may already contain user-tweaked values (provider, advanced flags, …).
_OH_SETTINGS_FILE="$OPENHANDS_STATE_DIR/settings.json"
if [ ! -f "$_OH_SETTINGS_FILE" ]; then
    # NOTE: OpenHands sandboxes can't resolve the compose-DNS name `litellm`
    # (they live on the default bridge), so we always pre-seed
    # `host.docker.internal:$LITELLM_HOST_PORT` here rather than the
    # in-cluster URL — same trick as compose.openhands.yml. Set
    # OPENHANDS_LITELLM_BASE_URL in .env.openhands to override.
    _oh_seed_base_url="${OPENHANDS_LITELLM_BASE_URL}"
    case "$_oh_seed_base_url" in
        *litellm:*) _oh_seed_base_url="http://host.docker.internal:${LITELLM_HOST_PORT}/v1" ;;
        '') _oh_seed_base_url="http://host.docker.internal:${LITELLM_HOST_PORT}/v1" ;;
    esac
    cat >"$_OH_SETTINGS_FILE" <<JSON
{
  "llm_model": "litellm_proxy/${OPENHANDS_DEFAULT_MODEL}",
  "llm_base_url": "${_oh_seed_base_url}",
  "llm_api_key": "${OPENHANDS_LITELLM_API_KEY}",
  "agent": "CodeActAgent",
  "language": "ru",
  "confirmation_mode": false
}
JSON
    chmod 600 "$_OH_SETTINGS_FILE" 2>/dev/null || true
    echo "→ Pre-seeded $_OH_SETTINGS_FILE (skip API-key onboarding modal)"
fi

# ── 5.5. Workspace ACL ─────────────────────────────────────────────────────
# The agent-server runtime image runs as the non-root user `openhands`
# (uid 10001 in agent-server:1.15.0-python — verify with `docker exec
# <oh-agent-server-...> id`). Our default OPENHANDS_WORKSPACE_DIR is
# $HOME/agent_dev with mode 750 owned by your host user — that puts
# uid 10001 into the "others" bucket (`---`), and on a fresh sandbox the
# very first `pydantic_settings` import inside the runtime tries to
# stat('.env') in cwd and dies with `PermissionError: [Errno 13]`:
#
#   File "fastmcp/__init__.py", line 14, in <module>
#   File "pydantic_settings/sources/providers/dotenv.py", line 100, ...
#   PermissionError: [Errno 13] Permission denied: '.env'
#
# Fix is POSIX ACL: keep your host user as owner (so git/IDE/fsbox keep
# working as before), and add an extra rwx entry for the runtime uid plus
# a default ACL so files the agent creates inherit access for the host user too.
# Skip silently if the FS or kernel does not support ACLs (e.g. tmpfs,
# overlay without xattr) — the launcher should never refuse to start
# because of an optional convenience tweak.
OPENHANDS_RUNTIME_UID="${OPENHANDS_RUNTIME_UID:-}"
if [ -z "$OPENHANDS_RUNTIME_UID" ]; then
    _live_runtime="$(docker ps --filter name=oh-agent-server --filter status=running \
        --format '{{.Names}}' 2>/dev/null | head -1)"
    if [ -n "$_live_runtime" ]; then
        OPENHANDS_RUNTIME_UID="$(docker exec "$_live_runtime" id -u 2>/dev/null || true)"
        OPENHANDS_RUNTIME_GID="$(docker exec "$_live_runtime" id -g 2>/dev/null || true)"
    fi
fi
OPENHANDS_RUNTIME_UID="${OPENHANDS_RUNTIME_UID:-10001}"
OPENHANDS_RUNTIME_GID="${OPENHANDS_RUNTIME_GID:-$OPENHANDS_RUNTIME_UID}"

if command -v setfacl >/dev/null 2>&1; then
    echo "→ Applying POSIX ACL on $OPENHANDS_WORKSPACE_DIR for runtime uid $OPENHANDS_RUNTIME_UID …"
    if setfacl -R \
            -m "u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx" \
            "$OPENHANDS_WORKSPACE_DIR" 2>/dev/null \
       && setfacl -R -d \
            -m "u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx,u:1000:rwx,g:1000:rwx" \
            "$OPENHANDS_WORKSPACE_DIR" 2>/dev/null; then
        echo "   ✓ ACL ok"
    else
        cat >&2 <<EOF
⚠  setfacl не отработал (нет прав, или ФС без ACL — например, tmpfs/overlay без xattr).
    Если новые conversation'ы будут падать с PermissionError на /workspace/project/.env,
    запустите вручную как root:
       sudo setfacl -R  -m u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx $OPENHANDS_WORKSPACE_DIR
       sudo setfacl -R -dm u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx,u:1000:rwx,g:1000:rwx $OPENHANDS_WORKSPACE_DIR
EOF
    fi
else
    cat >&2 <<EOF
⚠  setfacl не установлен. Workspace = $OPENHANDS_WORKSPACE_DIR
    Если runtime упадёт с PermissionError при открытии нового чата:
       sudo apt-get install -y acl
       sudo setfacl -R  -m u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx $OPENHANDS_WORKSPACE_DIR
       sudo setfacl -R -dm u:${OPENHANDS_RUNTIME_UID}:rwx,g:${OPENHANDS_RUNTIME_GID}:rwx,u:1000:rwx,g:1000:rwx $OPENHANDS_WORKSPACE_DIR
EOF
fi

# ── 6. Start OpenHands ─────────────────────────────────────────────────────
echo ""
echo "→ Запускаю OpenHands (docker compose up -d) …"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --pull missing

# ── 7. Wait for HTTP readiness ─────────────────────────────────────────────
echo ""
echo "→ Ожидаю готовности UI на http://localhost:$OPENHANDS_HOST_PORT/ …"
TIMEOUT=180
ELAPSED=0
until curl -fsS -m 3 "http://localhost:$OPENHANDS_HOST_PORT/" >/dev/null 2>&1; do
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo ""
        echo "⚠  OpenHands не ответил за ${TIMEOUT}s — проверьте логи:"
        echo "   docker logs openhands --tail 60"
        exit 1
    fi
    printf "."
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done
echo ""
echo "   ✓ OpenHands готов"

# ── 8. Summary ─────────────────────────────────────────────────────────────
cat <<EOF

==========================================
 OpenHands UI:  http://localhost:$OPENHANDS_HOST_PORT
 Через LiteLLM: $OPENHANDS_LITELLM_BASE_URL
 Default model: litellm_proxy/$OPENHANDS_DEFAULT_MODEL
 Workspace:     $OPENHANDS_WORKSPACE_DIR (внутри runtime: /workspace/project)
 State:         $OPENHANDS_STATE_DIR (внутри контейнера: /.openhands)

 В UI Settings → LLM → Advanced подставлено через env:
   Custom Model:  litellm_proxy/$OPENHANDS_DEFAULT_MODEL
   Base URL:      $OPENHANDS_LITELLM_BASE_URL
   API Key:       (LITELLM_API_KEY из .env)
 Можно поменять прямо в UI — оно перепишет переменные окружения.

 Остановить:    bash $SCRIPT_DIR/openhands-stop.sh
==========================================
EOF
