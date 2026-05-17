#!/usr/bin/env bash
# Start a host-side jupyter_server bound to the project root, used by OpenWebUI's
# CODE_EXECUTION_* / CODE_INTERPRETER_* integrations.
#
# Subcommands: start | stop | status | logs (default: start)
# Token is read from .env.openwebui (JUPYTER_TOKEN). The same token is
# passed through to OpenWebUI via compose.openwebui.yml.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
OPENWEBUI_ENV_FILE="$SCRIPT_DIR/.env.openwebui"

VENV_DIR="$SCRIPT_DIR/.venv-jupyter"
CONFIG_FILE="$SCRIPT_DIR/docker/jupyter/jupyter_server_config.py"
RUNTIME_DIR="$SCRIPT_DIR/docker/jupyter/runtime"
PID_FILE="$RUNTIME_DIR/jupyter.pid"
LOG_FILE="$RUNTIME_DIR/jupyter.log"

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
    JUPYTER_TOKEN \
    JUPYTER_HOST \
    JUPYTER_PORT \
    JUPYTER_ROOT_DIR
load_selected_env "$OPENWEBUI_ENV_FILE" \
    JUPYTER_TOKEN \
    JUPYTER_HOST \
    JUPYTER_PORT \
    JUPYTER_ROOT_DIR

JUPYTER_HOST="${JUPYTER_HOST:-127.0.0.1}"
JUPYTER_PORT="${JUPYTER_PORT:-8888}"
JUPYTER_ROOT_DIR="${JUPYTER_ROOT_DIR:-$HOME/agent_dev}"

say()  { echo -e "\033[1;36m→\033[0m $*"; }
ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

is_running() {
    [ -s "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

ensure_venv() {
    if [ ! -x "$VENV_DIR/bin/jupyter" ]; then
        die "venv $VENV_DIR не подготовлен (запустите phase 2a setup; см. docs/openwebui-tools.md)"
    fi
}

cmd_start() {
    ensure_venv
    [ -f "$CONFIG_FILE" ] || die "Не найден $CONFIG_FILE"
    [ -n "${JUPYTER_TOKEN:-}" ] || die "JUPYTER_TOKEN не задан (.env.openwebui)"

    # Refuse to run as root — Jupyter prints «Running as root is not recommended»
    # and refuses to start without --allow-root. We also do not want kernels with
    # uid 0 writing into $HOME/agent_dev (file ownership would diverge from the
    # host user and break IDE access). Re-invoke as your host user via sudo.
    if [ "$(id -u)" = "0" ]; then
        die "Не запускайте jupyter-host-start.sh из-под root. Перезапустите от своего юзера: sudo -u \$YOUR_USER $0 ${1:-start}"
    fi

    mkdir -p "$RUNTIME_DIR"

    # The Jupyter root_dir is a real directory on the host; create it if it is
    # the default agent_dev sandbox and is missing.
    if [ ! -d "$JUPYTER_ROOT_DIR" ]; then
        if [ "$JUPYTER_ROOT_DIR" = "$HOME/agent_dev" ]; then
            warn "$JUPYTER_ROOT_DIR отсутствует — создаю c правами 0750"
            mkdir -p "$JUPYTER_ROOT_DIR"
            chmod 0750 "$JUPYTER_ROOT_DIR"
        else
            die "JUPYTER_ROOT_DIR=$JUPYTER_ROOT_DIR не существует"
        fi
    fi

    if is_running; then
        ok "jupyter_server уже бежит (pid $(cat "$PID_FILE"))"
        return 0
    fi

    say "Стартую jupyter_server на $JUPYTER_HOST:$JUPYTER_PORT (root=$JUPYTER_ROOT_DIR)"
    cd "$SCRIPT_DIR"
    JUPYTER_TOKEN="$JUPYTER_TOKEN" \
    JUPYTER_HOST="$JUPYTER_HOST" \
    JUPYTER_PORT="$JUPYTER_PORT" \
    JUPYTER_ROOT_DIR="$JUPYTER_ROOT_DIR" \
    nohup "$VENV_DIR/bin/jupyter" server \
        --config="$CONFIG_FILE" \
        >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 1
    for _ in {1..20}; do
        if curl -fsS -m 2 \
            "http://$JUPYTER_HOST:$JUPYTER_PORT/api?token=$JUPYTER_TOKEN" \
            >/dev/null 2>&1; then
            ok "jupyter_server отвечает на /api (pid $(cat "$PID_FILE"))"
            return 0
        fi
        sleep 1
    done
    warn "jupyter_server не подтвердил готовность за 20s — см. $LOG_FILE"
}

cmd_stop() {
    if ! is_running; then
        ok "jupyter_server не запущен"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    say "Останавливаю jupyter_server (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in {1..15}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            ok "jupyter_server остановлен"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done
    warn "kill -TERM не сработал, пробую -KILL"
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
}

cmd_status() {
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        ok "jupyter_server бежит (pid $pid) на $JUPYTER_HOST:$JUPYTER_PORT (root=$JUPYTER_ROOT_DIR)"
        if curl -fsS -m 2 \
            "http://$JUPYTER_HOST:$JUPYTER_PORT/api?token=$JUPYTER_TOKEN" \
            >/dev/null 2>&1; then
            ok "/api отвечает с текущим JUPYTER_TOKEN"
        else
            warn "/api не отвечает; см. $LOG_FILE"
        fi
    else
        warn "jupyter_server не запущен (root_dir будет $JUPYTER_ROOT_DIR)"
    fi
}

cmd_logs() {
    [ -f "$LOG_FILE" ] || { warn "лог отсутствует: $LOG_FILE"; return 0; }
    tail -n "${1:-200}" "$LOG_FILE"
}

case "${1:-start}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status) cmd_status ;;
    logs)   shift || true; cmd_logs "${1:-200}" ;;
    *) die "Использование: $0 {start|stop|restart|status|logs [N]}" ;;
esac
