#!/usr/bin/env bash
# dify-stop.sh — гасит Dify-стек, поднятый dify-start.sh.
# Volumes (Postgres, Weaviate, plugin storage) НЕ удаляются — это
# пользовательские данные. Чтобы стереть всё начисто:
#   bash dify-stop.sh --purge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIFY_DIR="$SCRIPT_DIR/dify/docker"
COMPOSE_OVERRIDE="$SCRIPT_DIR/compose.dify.yml"

PURGE=0
if [ "${1:-}" = "--purge" ]; then
    PURGE=1
fi

ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
say()  { printf "\033[1;34m→\033[0m %s\n" "$*"; }

if [ ! -d "$DIFY_DIR" ] || [ ! -f "$DIFY_DIR/docker-compose.yaml" ]; then
    say "Dify caталог не найден ($DIFY_DIR) — нечего гасить."
    exit 0
fi

cd "$DIFY_DIR"
if [ "$PURGE" = "1" ]; then
    say "Гашу Dify + удаляю volumes (PURGE)"
    docker compose \
        -f docker-compose.yaml \
        -f "$COMPOSE_OVERRIDE" \
        --project-name dify \
        down -v
    ok "Dify остановлен, volumes стёрты"
else
    say "Гашу Dify (volumes сохраняются)"
    docker compose \
        -f docker-compose.yaml \
        -f "$COMPOSE_OVERRIDE" \
        --project-name dify \
        down
    ok "Dify остановлен (для удаления данных: bash dify-stop.sh --purge)"
fi
