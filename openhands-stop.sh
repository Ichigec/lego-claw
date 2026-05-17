#!/usr/bin/env bash
# Stop OpenHands without touching the rest of the stack.
# Run from the project root: bash openhands-stop.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.openhands.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "compose.openhands.yml не найден: $COMPOSE_FILE" >&2
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

echo "→ Останавливаю OpenHands …"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down

echo "✓ OpenHands остановлен."
echo "  Состояние сохранено в \${OPENHANDS_STATE_DIR:-\$HOME/.openhands};"
echo "  workspace в \${OPENHANDS_WORKSPACE_DIR:-\$HOME/agent_dev} остаётся нетронутым."
