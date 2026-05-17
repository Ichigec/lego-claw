#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCALAI_HOST_PORT="${LOCALAI_HOST_PORT:-8180}"

echo "=== LocalAI + qwen3.6 legacy profile ==="
echo "→ Возвращаю qwen3.6-35b-heretic в LocalAI вручную"
echo "→ Основной маршрут через LiteLLM/LM Studio при этом не меняется"
echo

LOCALAI_ENABLE_QWEN36=1 bash "$SCRIPT_DIR/localai-start.sh"

echo
echo "→ Проверяю, что qwen3.6-35b-heretic виден в реестре LocalAI"
if curl -fsS "http://localhost:$LOCALAI_HOST_PORT/v1/models" | grep -q '"id":"qwen3.6-35b-heretic"'; then
    echo "✓ qwen3.6-35b-heretic доступен в LocalAI"
else
    echo "⚠ qwen3.6-35b-heretic не найден в /v1/models" >&2
    exit 1
fi

echo
echo "Подсказка: чтобы сохранить legacy-профиль при полном bootstrap,"
echo "используйте LOCALAI_ENABLE_QWEN36=1 bash \"$SCRIPT_DIR/stack-start.sh\""
