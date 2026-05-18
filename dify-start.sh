#!/usr/bin/env bash
# dify-start.sh — launcher для опционального кубика Dify
# (визуальный no-code редактор workflow'ов:
# https://github.com/langgenius/dify, версия 1.13.3).
#
# Поднимает upstream `dify/docker/docker-compose.yaml` (vendored
# из langgenius/dify@1.13.3), накладывая наш override
# `compose.dify.yml` (порт nginx → 127.0.0.1:8090, подключение к
# `llm-stack-net`).
#
# После первого запуска зарегистрируйте LiteLLM и agent-mesh
# адаптеры в Dify:
#   bash scripts/dify-register-litellm.sh        # Custom OpenAI provider
#   bash scripts/dify-register-agent-mesh.sh     # 3 Custom Tools
#
# Запуск из корня репо: bash dify-start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIFY_DIR="$SCRIPT_DIR/dify/docker"
COMPOSE_OVERRIDE="$SCRIPT_DIR/compose.dify.yml"
ENV_DIFY_EXAMPLE="$SCRIPT_DIR/.env.dify.example"

DIFY_HOST_PORT="${DIFY_HOST_PORT:-8090}"

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }
say()  { printf "\033[1;34m→\033[0m %s\n" "$*"; }

# ── 1. Sanity checks ──────────────────────────────────────────────────────
[ -d "$DIFY_DIR" ]               || die "Каталог $DIFY_DIR отсутствует. Восстановите vendored Dify через git clone или скрипт восстановления (см. план)."
[ -f "$DIFY_DIR/docker-compose.yaml" ] \
    || die "Не найден $DIFY_DIR/docker-compose.yaml. Скачайте из langgenius/dify@1.13.3 (см. INSTALL.md → Опциональные кубики → Dify)."
[ -f "$COMPOSE_OVERRIDE" ]       || die "Не найден $COMPOSE_OVERRIDE."

if ! docker info >/dev/null 2>&1; then
    die "Docker недоступен из текущей сессии. sudo usermod -aG docker \$USER && newgrp docker"
fi

if ! docker network inspect llm-stack-net >/dev/null 2>&1; then
    die "Сеть llm-stack-net не найдена. Сначала: bash $SCRIPT_DIR/stack-start.sh"
fi

# ── 2. Создать dify/docker/.env из upstream-шаблона при первом запуске ───
DIFY_ENV="$DIFY_DIR/.env"
if [ ! -f "$DIFY_ENV" ]; then
    if [ -f "$DIFY_DIR/.env.example" ]; then
        cp "$DIFY_DIR/.env.example" "$DIFY_ENV"
        ok "Создан $DIFY_ENV из .env.example"
    else
        die "Не найден ни $DIFY_ENV, ни $DIFY_DIR/.env.example. Восстановите upstream-шаблон."
    fi
fi

# Прокинуть наш порт nginx в dify/docker/.env (Dify читает EXPOSE_NGINX_PORT
# при работе скрипта nginx/docker-entrypoint.sh — порт самого upstream
# bindings уже override'ится через compose.dify.yml, но переменная
# нужна для согласованности логов и URL'ов).
if grep -q '^EXPOSE_NGINX_PORT=' "$DIFY_ENV"; then
    sed -i "s|^EXPOSE_NGINX_PORT=.*|EXPOSE_NGINX_PORT=$DIFY_HOST_PORT|" "$DIFY_ENV"
else
    printf "\nEXPOSE_NGINX_PORT=%s\n" "$DIFY_HOST_PORT" >>"$DIFY_ENV"
fi

# Подсказать пользователю про .env.dify.example, если он ещё не создал .env.dify.
if [ ! -f "$SCRIPT_DIR/.env.dify" ] && [ -f "$ENV_DIFY_EXAMPLE" ]; then
    warn ".env.dify не создан — для регистрационных скриптов скопируйте: cp $ENV_DIFY_EXAMPLE $SCRIPT_DIR/.env.dify"
fi

# ── 3. Поднять Dify ──────────────────────────────────────────────────────
say "Запускаю Dify (compose: docker-compose.yaml + ../../compose.dify.yml)"
cd "$DIFY_DIR"
docker compose \
    -f docker-compose.yaml \
    -f "$COMPOSE_OVERRIDE" \
    --project-name dify \
    up -d

# ── 4. Дождаться готовности UI ────────────────────────────────────────────
say "Жду готовности UI на http://localhost:$DIFY_HOST_PORT/ ..."
TIMEOUT=180
ELAPSED=0
until curl -fsS -m 3 "http://localhost:$DIFY_HOST_PORT/" >/dev/null 2>&1; do
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        warn "Dify не ответил за ${TIMEOUT}s — проверьте логи: docker logs docker-nginx-1 --tail 60 (или docker-api-1)."
        exit 1
    fi
    printf "."
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done
echo
ok "Dify готов"

# ── 5. Summary ───────────────────────────────────────────────────────────
cat <<EOF

==========================================
 Dify UI:           http://localhost:$DIFY_HOST_PORT
 Compose project:   dify
 Vendored upstream: langgenius/dify-api:1.13.3
                    langgenius/dify-web:1.13.3

 Первый запуск:
   1) откройте UI → создайте admin'а (форма /install)
   2) войдите → Settings → Profile → API Keys → создайте Console Token,
      положите его в .env.dify (DIFY_CONSOLE_TOKEN=...)
   3) bash scripts/dify-register-litellm.sh    # LiteLLM как provider
   4) bash scripts/dify-register-agent-mesh.sh # 3 наших адаптера как Tools
   5) (опц.) Studio → Import DSL → examples/dify-workflow-agent-mesh.yml

 Документация: docs/dify.md
 Остановить:   bash dify-stop.sh
==========================================
EOF
