#!/usr/bin/env bash
# Stop opencode without touching the rest of the stack.
# Run from the project root: bash opencode-stop.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.opencode}"
COMPOSE_FILE="$SCRIPT_DIR/compose.opencode.yml"
OPENCODE_COMPOSE_PROJECT="${OPENCODE_COMPOSE_PROJECT:-opencode}"
if [ -f "$ENV_OVERRIDE_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue ;;
        esac
        key="${line%%=*}"
        [ "$key" = "OPENCODE_COMPOSE_PROJECT" ] || continue
        value="${line#*=}"
        key="${key%$'\r'}"
        value="${value%$'\r'}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export OPENCODE_COMPOSE_PROJECT="$value"
        break
    done < "$ENV_OVERRIDE_FILE"
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "compose.opencode.yml не найден: $COMPOSE_FILE" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    cat >&2 <<EOF
Docker недоступен из текущей сессии.
Если пользователь ещё не в группе docker:
  sudo usermod -aG docker "${USER:-$LOGNAME}" && newgrp docker
EOF
    exit 1
fi

# Если внутри контейнера крутится `opencode web` — кладём его сначала,
# чтобы лог был аккуратнее (compose down всё равно убил бы контейнер,
# но так пользователь видит, что web UI ушёл).
if [ "$(docker inspect --format='{{.State.Status}}' opencode 2>/dev/null || echo missing)" = "running" ]; then
    if docker exec opencode pgrep -fa 'opencode web' >/dev/null 2>&1; then
        echo "→ Останавливаю opencode web (UI на :\${OPENCODE_WEB_HOST_PORT:-3400}) …"
        bash "$SCRIPT_DIR/opencode-web-start.sh" --stop || true
    fi
fi

echo "→ Останавливаю opencode …"
docker compose -p "$OPENCODE_COMPOSE_PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down

echo "✓ opencode остановлен."
echo "  Состояние сохранено в \${OPENCODE_STATE_DIR:-\$HOME/.opencode};"
echo "  workspace в \${OPENCODE_WORKSPACE_DIR:-\$HOME/agent_dev} остаётся нетронутым."
