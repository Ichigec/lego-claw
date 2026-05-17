#!/usr/bin/env bash
# Publish the optional `opencode web` HTTP UI on 127.0.0.1:${OPENCODE_WEB_HOST_PORT:-3400}.
#
# Parallel to the OpenHands GUI publish on :3300 — gives operators a
# browser entry point alongside the TUI / ACP bridge. Idempotent:
# re-running just kills the previous `opencode web` process inside the
# container and re-launches with the latest env.
#
# Prerequisites: bash opencode-start.sh --no-attach (the long-lived
# container must be running so we can `docker exec` into it). The web
# port is reserved by compose.opencode.yml (`127.0.0.1:WEB_HOST_PORT:4096`)
# even when the web server isn't running, so curl / browser bookmarks
# stay stable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OPENCODE="$SCRIPT_DIR/.env.opencode"

_OC_KEYS=(
    OPENCODE_WEB_HOST_PORT
    OPENCODE_LITELLM_BASE_URL
    OPENCODE_LITELLM_API_KEY
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

OPENCODE_WEB_HOST_PORT="${OPENCODE_WEB_HOST_PORT:-3400}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

ACTION=start
for arg in "$@"; do
    case "$arg" in
        --stop) ACTION=stop ;;
        --restart) ACTION=restart ;;
        -h|--help)
            cat <<USAGE
Usage: bash $0 [--stop|--restart]
  (no args)   запустить (или ничего не делать, если уже работает)
  --stop      убить процесс opencode web внутри контейнера
  --restart   --stop + start
URL после старта: http://127.0.0.1:$OPENCODE_WEB_HOST_PORT
USAGE
            exit 0
            ;;
    esac
done

if ! docker info >/dev/null 2>&1; then
    die "Docker недоступен. Добавьте пользователя в группу docker и перелогиньтесь."
fi
if [ "$(docker inspect --format='{{.State.Status}}' opencode 2>/dev/null || echo missing)" != "running" ]; then
    die "Контейнер opencode не запущен. Сначала: bash $SCRIPT_DIR/opencode-start.sh --no-attach"
fi

stop_web() {
    if docker exec opencode pgrep -fa 'opencode web' >/dev/null 2>&1; then
        docker exec opencode pkill -f 'opencode web' || true
        sleep 1
        if docker exec opencode pgrep -fa 'opencode web' >/dev/null 2>&1; then
            warn "процессы opencode web не убились с SIGTERM — посылаю SIGKILL"
            docker exec opencode pkill -9 -f 'opencode web' || true
        fi
        ok "opencode web остановлен внутри контейнера"
    else
        warn "opencode web не работал — нечего останавливать"
    fi
}

_oc_binary_path() {
    # opencode'овский installer кладёт бинарь в одно из этих мест.
    # Берём первый существующий — fallback на голое `opencode` (если
    # ENV PATH из Dockerfile вдруг сработал и login-shell не отбросил его).
    docker exec opencode bash -c '
        for p in /home/opencode/.local/bin/opencode \
                 /home/opencode/.opencode/bin/opencode \
                 /usr/local/bin/opencode; do
            if [ -x "$p" ]; then printf "%s\n" "$p"; exit 0; fi
        done
        command -v opencode 2>/dev/null || true
    ' 2>/dev/null | tr -d "\r\n"
}

_oc_web_running() {
    # Несколько способов проверить, что web UI поднят:
    # 1. pgrep (если установлен procps в образе — после P3-фикса в Dockerfile)
    # 2. curl http://127.0.0.1:4096/ изнутри контейнера (всегда доступен,
    #    т.к. opencode-образ ставит curl)
    if docker exec opencode pgrep -fa 'opencode web' >/dev/null 2>&1; then
        return 0
    fi
    if docker exec opencode curl -fsS -m 2 http://127.0.0.1:4096/ >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

start_web() {
    if _oc_web_running; then
        warn "opencode web уже работает — не запускаю повторно. Используйте --restart."
        return
    fi

    local oc_bin
    oc_bin="$(_oc_binary_path)"
    if [ -z "$oc_bin" ]; then
        die "Не нашёл бинарь opencode внутри контейнера (искал в ~/.local/bin, ~/.opencode/bin, /usr/local/bin). Пересоберите образ: bash opencode-start.sh"
    fi
    ok "Бинарь opencode внутри контейнера: $oc_bin"

    # Усекаем лог, чтобы tail при ошибке показывал ТОЛЬКО текущий запуск,
    # а не накопившийся мусор от прошлых неудачных попыток.
    docker exec opencode bash -c ': > /.opencode/web.log' 2>/dev/null || true

    # `-d` detaches inside the container так, чтобы шелл не висел на демоне.
    # Без `-l` намеренно: login-shell в Debian пере-исходит /etc/profile и
    # сбрасывает PATH, выкидывая ~/.local/bin (куда installer кладёт opencode).
    # Поэтому вызываем opencode по абсолютному пути.
    # Порт 4096 — дефолт opencode web (см. `opencode web --help`).
    docker exec -d opencode bash -c "cd /workspace/project && exec '$oc_bin' web --hostname 0.0.0.0 --port 4096 >>/.opencode/web.log 2>&1"

    # opencode web/serve поднимается ~1-3с в первый раз. Активно ждём
    # до 15 секунд, проверяя curl'ом изнутри контейнера.
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        sleep 1
        if _oc_web_running; then
            ok "opencode web запущен (бинарь: $oc_bin; внутри: :4096, хост: 127.0.0.1:$OPENCODE_WEB_HOST_PORT, за ${i}s)"
            return
        fi
    done

    tail_log="$(docker exec opencode tail -n 30 /.opencode/web.log 2>/dev/null || true)"
    die "opencode web не стартовал за 15s. Последние строки лога:\n$tail_log"
}

case "$ACTION" in
    stop) stop_web ;;
    restart) stop_web; start_web ;;
    start) start_web ;;
esac

if [ "$ACTION" != "stop" ]; then
    echo
    echo "URL:        http://127.0.0.1:$OPENCODE_WEB_HOST_PORT"
    echo "Логи:       docker exec opencode tail -f /.opencode/web.log"
    echo "Остановить: bash $0 --stop"
fi
