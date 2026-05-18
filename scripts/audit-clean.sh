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
# Если каталог из forbidden существует И содержит хотя бы один обычный файл,
# валим аудит. Если каталог в .gitignore И пустой / содержит только пустые
# поддиректории — это безвредный runtime-leftover (например пустой
# `librechat/data-node` от давно удалённого compose-volume), git его не
# трогает; такое подсвечиваем как warn-only.
echo "5. Удалённые каталоги, которые не должны существовать:"
forbidden=(
    "librechat"
    "final_description"
    ".venv_g"
    ".venv-jupyter"
    ".venv-sherpa"
)
found=""
warn_leftover=""
for d in "${forbidden[@]}"; do
    [ -e "$d" ] || continue
    if [ -d "$d" ] && [ -z "$(find "$d" -type f 2>/dev/null | head -1)" ] \
            && git check-ignore -q "$d" 2>/dev/null; then
        warn_leftover="$warn_leftover  $d (пустой gitignored leftover)\n"
        continue
    fi
    found="$found  существует: $d\n"
done
check "no removed directories present" "$(printf '%b' "$found")"
if [ -n "$warn_leftover" ]; then
    printf "  %s!%s warn: пустые leftover-каталоги (gitignored, не пушатся, но удалите вручную):\n" "$YEL" "$RST"
    printf '%b' "$warn_leftover" | sed 's|^|    |'
fi

# ── 6. .env* файлы не трекаются git'ом (только .env*.example) ───────────────
echo "6. .env* (без .example) не должны быть в git:"
found="$(git ls-files | grep -E '^\.env(\.[a-z]+)?$' | grep -v '\.example$' | head -10)"
check "no .env* (non-example) files tracked by git" "$found"

# ── 7. PNG/WAV/MP3 в корне репо ─────────────────────────────────────────────
echo "7. Бинарные артефакты в корне репо (не в моделях):"
found="$(find . -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.wav' -o -iname '*.mp3' \) 2>/dev/null)"
check "no PNG/WAV/MP3 in repo root" "$found"

# ── 8. Неизвестные контейнеры в llm-stack-net (warn-only) ───────────────────
# Если в нашей docker-сети висят контейнеры, которых нет в compose-файлах
# репо, это либо чужой стек (voice-assistant, gpu-tts, whisper-stt, …),
# либо забытые runtime-sandbox'ы OpenHands (oh-agent-server-…), либо
# Dify-сервисы (включаются опционально). Не валим аудит — просто
# подсвечиваем, чтобы пользователь сам решил.
echo "8. Неизвестные контейнеры в сети llm-stack-net (warn-only):"
if command -v docker >/dev/null 2>&1 \
        && docker network inspect llm-stack-net >/dev/null 2>&1; then
    # Список «наших» имён контейнеров — то, что объявляют compose.*.yml
    # (container_name) + типовые префиксы (oh-agent-server-* спавнится
    # OpenHands runtime-ом, dify-* — vendored Dify compose).
    known_re='^(phoenix|phoenix-db|litellm|litellm-db|openai-stack-relay'
    known_re+='|open-webui|searxng|searchbox|shellbox|fsbox|localai'
    known_re+='|llama-server|clawcode|clawcode-adapter'
    known_re+='|openhands|openhands-adapter|opencode|opencode-adapter'
    known_re+='|agent-registry|skills-manager'
    known_re+='|oh-agent-server-.*'
    known_re+='|docker-api-1|docker-worker-1|docker-web-1|docker-nginx-1'
    known_re+='|docker-redis-1|docker-db-1|docker-weaviate-1|docker-sandbox-1'
    known_re+='|docker-ssrf_proxy-1|docker-plugin_daemon-1)$'
    unknown="$(
        docker network inspect llm-stack-net \
            --format '{{range $i, $c := .Containers}}{{$c.Name}}
{{end}}' 2>/dev/null \
            | grep -v '^$' \
            | grep -vE "$known_re" \
            | head -10
    )"
    if [ -n "$unknown" ]; then
        printf "  %s!%s неизвестные контейнеры в llm-stack-net:\n" "$YEL" "$RST"
        printf "%s\n" "$unknown" | sed 's|^|      |'
        printf "      %s(warn-only — это либо чужой стек,%s\n" "$YEL" "$RST"
        printf "      %s либо забытый runtime-sandbox; не учитывается в exit code)%s\n" "$YEL" "$RST"
    else
        printf "  %s✓%s only known containers in llm-stack-net\n" "$GRN" "$RST"
    fi
else
    printf "  %s-%s skipped (docker недоступен или сеть llm-stack-net не существует)\n" "$YEL" "$RST"
fi

echo
if [ "$problems" -eq 0 ]; then
    printf "%sВсё чисто.%s Можно публиковать.\n" "$GRN" "$RST"
    exit 0
else
    printf "%sНайдено проблем: %d%s. Не пушьте до их устранения.\n" \
        "$RED" "$problems" "$RST"
    exit 1
fi
