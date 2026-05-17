#!/usr/bin/env bash
# scripts/lib/env-expand.sh — общая helper-функция для launcher'ов.
#
# Зачем: наши `.env*` файлы должны содержать пути типа `${HOME}/agent_dev`
# (а не захардкоженные `/home/<юзер>/agent_dev`), чтобы репо был
# переносимым между юзерами. Однако кастомные whitelist-парсеры в
# `clawcode-start.sh` / `openhands-start.sh` / `opencode-start.sh` /
# `jupyter-host-start.sh` читают значения литерально — `${HOME}` не
# раскрывается. Эта функция применяется к каждому пути-значению.
#
# Поддерживаемые формы:
#   ${HOME}/foo     →  $EFFECTIVE_HOME/foo
#   $HOME/foo       →  $EFFECTIVE_HOME/foo
#   ~/foo           →  $EFFECTIVE_HOME/foo
#   (всё остальное) →  как есть
#
# EFFECTIVE_HOME — это $HOME запускающего юзера; если скрипт запущен через
# `sudo bash …`, то $HOME может оказаться `/root`. Чтобы понять «настоящий»
# home, мы (1) предпочитаем `$SUDO_USER` (passwd-lookup), (2) иначе owner
# репо, (3) иначе $HOME. Соответствующий блок выставления EFFECTIVE_HOME
# уже есть в каждом *-start.sh — здесь мы только им пользуемся.
#
# Usage:
#   . "$(dirname "${BASH_SOURCE[0]}")/scripts/lib/env-expand.sh"
#   FOO_DIR="$(expand_home "${FOO_DIR}")"

expand_home() {
    local v="$1"
    local home="${EFFECTIVE_HOME:-$HOME}"
    case "$v" in
        '${HOME}'*) printf '%s' "${home}${v#\$\{HOME\}}" ;;
        '$HOME'*)   printf '%s' "${home}${v#\$HOME}" ;;
        '~/'*)      printf '%s' "${home}/${v#\~/}" ;;
        '~')        printf '%s' "${home}" ;;
        *)          printf '%s' "$v" ;;
    esac
}

# Установка EFFECTIVE_HOME: если ещё не задан внешним скриптом — выставим тут.
# Безопасно вызывать многократно: уже-определённое значение не перезаписывается.
ensure_effective_home() {
    if [ -n "${EFFECTIVE_HOME:-}" ]; then return 0; fi
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        EFFECTIVE_HOME="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
    elif [ "$(id -u)" = "0" ]; then
        local owner
        owner="$(stat -c '%U' "${SCRIPT_DIR:-$PWD}" 2>/dev/null || true)"
        if [ -n "$owner" ] && [ "$owner" != "root" ]; then
            EFFECTIVE_HOME="$(getent passwd "$owner" 2>/dev/null | cut -d: -f6)"
        fi
    fi
    EFFECTIVE_HOME="${EFFECTIVE_HOME:-$HOME}"
    export EFFECTIVE_HOME
}
