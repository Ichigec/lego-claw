#!/usr/bin/env bash
# Stop Claw Code without touching the rest of the stack.
# Run from the project root: bash clawcode-stop.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.clawcode}"
COMPOSE_FILE="$SCRIPT_DIR/compose.clawcode.yml"
CC_COMPOSE_PROJECT="${CC_COMPOSE_PROJECT:-clawcode}"
if [ -f "$ENV_OVERRIDE_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue ;;
        esac
        key="${line%%=*}"
        [ "$key" = "CC_COMPOSE_PROJECT" ] || continue
        value="${line#*=}"
        key="${key%$'\r'}"
        value="${value%$'\r'}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export CC_COMPOSE_PROJECT="$value"
        break
    done < "$ENV_OVERRIDE_FILE"
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "compose.clawcode.yml не найден: $COMPOSE_FILE" >&2
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

echo "→ Останавливаю Claw Code …"
docker compose -p "$CC_COMPOSE_PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down

echo "✓ Claw Code остановлен."
echo "  Состояние сохранено в \${CLAWCODE_STATE_DIR:-\$HOME/.clawcode};"
echo "  workspace в \${CLAWCODE_WORKSPACE_DIR:-\$HOME/agent_dev} остаётся нетронутым."
