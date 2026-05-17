#!/usr/bin/env bash
# scripts/audit-clean.sh — pre-publish sanity check.
#
# Запускайте перед каждым `git push`: скрипт ищет в трекаемых файлах
# (1) персональные пути типа /home/<user>/, (2) hex-секреты (>=16 hex)
# в .env*, (3) email-адреса в исходниках, (4) непустые runtime-каталоги
# (data/backups/, data/diagnostics/, …) которые должны быть пустыми.
#
# Возвращает exit code 0 если всё чисто, != 0 если найдены проблемы.
# Запускать из корня репо:  bash scripts/audit-clean.sh

set -u

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -P "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

RED=$'\033[1;31m'
GRN=$'\033[1;32m'
YEL=$'\033[1;33m'
RST=$'\033[0m'

problems=0

check() {
    local name="$1"; shift
    local found="$1"; shift
    if [ -z "$found" ]; then
        printf "  %s✓%s %s\n" "$GRN" "$RST" "$name"
    else
        printf "  %s✗%s %s\n" "$RED" "$RST" "$name"
        printf "%s\n" "$found" | sed 's|^|      |'
        problems=$((problems + 1))
    fi
}

if command -v rg >/dev/null 2>&1; then
    GREPPER=rg
else
    GREPPER=grep
fi

echo "=== audit-clean.sh: проверки перед публикацией ==="
echo

# ── 1. Персональные пути ────────────────────────────────────────────────────
# Контейнерные юзеры (agent, searcher, runner, openhands, opencode, clawcode,
# jovyan, node, root) — это in-container HOME-пути из Dockerfile'ов;
# к персональным данным пользователя репо не относятся, поэтому исключаем.
echo "1. Персональные /home/<user>/ пути:"
container_users='agent|searcher|runner|openhands|opencode|clawcode|jovyan|node|root|app|user'
found="$( {
    git ls-files | while IFS= read -r f; do
        [ -f "$f" ] || continue
        case "$f" in
            *.env|*.env.*) continue ;;
            scripts/audit-clean.sh) continue ;;
            README.md) continue ;;
        esac
        grep -nE '/home/[a-z][a-z0-9_-]+' "$f" 2>/dev/null \
            | grep -vE "/home/($container_users)(/|\$|[^a-z0-9_-])" \
            | grep -v 'EXAMPLE\|example\|placeholder' \
            | sed "s|^|$f:|" \
            | head -3
    done
} | head -30 )"
check "no personal /home/<user>/ paths in tracked files" "$found"

# ── 2. Hex-секреты в .env* ──────────────────────────────────────────────────
echo "2. Hex-секреты (>=16 hex) в .env*.example:"
found="$( {
    for f in .env*.example; do
        [ -f "$f" ] || continue
        # длинные hex-значения; placeholder вида __GENERATE_…__ не матчит (там __)
        grep -nE '^[A-Z_][A-Z0-9_]*=[0-9a-fA-F]{16,}$' "$f" 2>/dev/null \
            | sed "s|^|$f:|"
    done
} | head -30 )"
check "no hex secrets in .env.example templates" "$found"

# ── 3. Email-адреса в трекаемых файлах ──────────────────────────────────────
echo "3. Email-адреса в трекаемых файлах:"
found="$( {
    git ls-files | while IFS= read -r f; do
        [ -f "$f" ] || continue
        case "$f" in
            *.env.example) continue ;;
            README.md) continue ;;  # пример "admin@example.org" OK
            scripts/audit-clean.sh) continue ;;
            docker/searxng/settings.yml) continue ;;  # содержит admin@admin.com из upstream
            mcp/openhands_mcp.json) continue ;;
            docs/*.md) continue ;;  # допустимы примеры user@example.com
        esac
        grep -nE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' "$f" 2>/dev/null \
            | grep -viE 'example\.(com|org|net)|noreply|admin@admin|@github|@litellm' \
            | grep -v '^Binary' \
            | head -3
    done
} | head -30 )"
check "no personal emails in tracked files" "$found"

# ── 4. Непустые runtime-каталоги (должны содержать максимум .gitkeep/README.md) ──
echo "4. Непустые runtime-каталоги:"
runtime_dirs=(
    "data/backups"
    "data/debug"
    "data/diagnostics"
    "data/trajectories"
    "docker/jupyter/runtime"
)
found=""
for d in "${runtime_dirs[@]}"; do
    [ -d "$d" ] || continue
    # допустимый набор: только .gitkeep и README.md
    extra="$(find "$d" -mindepth 1 -maxdepth 1 \
        ! -name '.gitkeep' ! -name 'README.md' 2>/dev/null)"
    if [ -n "$extra" ]; then
        found="$found  $d содержит:\n$(echo "$extra" | sed 's|^|    |')\n"
    fi
done
check "runtime dirs only contain .gitkeep/README.md" "$(printf '%b' "$found")"

# ── 5. Артефакты, которые должны быть удалены целиком ───────────────────────
echo "5. Удалённые каталоги, которые не должны существовать:"
forbidden=(
    "librechat"
    "final_description"
    ".venv_g"
    ".venv-jupyter"
    ".venv-sherpa"
)
found=""
for d in "${forbidden[@]}"; do
    if [ -e "$d" ]; then
        found="$found  существует: $d\n"
    fi
done
check "no removed directories present" "$(printf '%b' "$found")"

# ── 6. .env* файлы не трекаются git'ом (только .env*.example) ───────────────
echo "6. .env* (без .example) не должны быть в git:"
found="$(git ls-files | grep -E '^\.env(\.[a-z]+)?$' | head -10)"
check "no .env* files tracked by git" "$found"

# ── 7. PNG/WAV/MP3 в корне репо ─────────────────────────────────────────────
echo "7. Бинарные артефакты в корне репо (не в моделях):"
found="$(find . -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.wav' -o -iname '*.mp3' \) 2>/dev/null)"
check "no PNG/WAV/MP3 in repo root" "$found"

echo
if [ "$problems" -eq 0 ]; then
    printf "%sВсё чисто.%s Можно публиковать.\n" "$GRN" "$RST"
    exit 0
else
    printf "%sНайдено проблем: %d%s. Не пушьте до их устранения.\n" \
        "$RED" "$problems" "$RST"
    exit 1
fi
