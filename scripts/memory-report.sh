#!/usr/bin/env bash
# Memory report — собирает GPU/host/docker/disk-снимки в один markdown.
#
# Использование:
#   bash scripts/memory-report.sh             # снять текущее в data/diagnostics/memory-report-<ts>.md
#   bash scripts/memory-report.sh --baseline  # сохранить как BEFORE (data/diagnostics/memory-baseline.md)
#   bash scripts/memory-report.sh --diff      # печатает дельту BEFORE → AFTER
#   bash scripts/memory-report.sh --output PATH  # явно задать путь отчёта
#
# Скрипт идемпотентен: ничего не запускает, не лезет в Docker сетки и
# контейнеры, кроме `docker stats --no-stream` и `docker images`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_RUNTIME="$SCRIPT_DIR/.env.runtime"
ENV_OPENWEBUI="$SCRIPT_DIR/.env.openwebui"

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

load_selected_env "$ENV_FILE" LMSTUDIO_MODELS_DIR
load_selected_env "$ENV_RUNTIME" LMSTUDIO_MODELS_DIR
load_selected_env "$ENV_OPENWEBUI" \
    SPEACHES_HOST_PORT \
    OPENEDAI_SPEECH_HOST_PORT \
    KOKORO_HOST_PORT

LMSTUDIO_MODELS_DIR="${LMSTUDIO_MODELS_DIR:-$HOME/.lmstudio/models}"

MODE="snapshot"
OUTPUT=""
BASELINE_DEFAULT="$SCRIPT_DIR/data/diagnostics/memory-baseline.md"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --baseline) MODE="baseline" ;;
        --diff)     MODE="diff" ;;
        --output)   OUTPUT="$2"; shift ;;
        --output=*) OUTPUT="${1#*=}" ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
    shift
done

TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SCRIPT_DIR/data/diagnostics"

if [ -z "$OUTPUT" ]; then
    case "$MODE" in
        baseline) OUTPUT="$BASELINE_DEFAULT" ;;
        diff)     OUTPUT="$SCRIPT_DIR/data/diagnostics/memory-diff-${TS}.md" ;;
        *)        OUTPUT="$SCRIPT_DIR/data/diagnostics/memory-report-${TS}.md" ;;
    esac
fi

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }

snapshot() {
    local target="$1"
    {
        echo "# Memory report — $TS"
        echo ""
        echo "host: \`$(hostname)\`  cwd: \`$SCRIPT_DIR\`"
        echo ""

        echo "## nvidia-smi"
        echo ""
        echo '```'
        if command -v nvidia-smi >/dev/null 2>&1; then
            nvidia-smi 2>/dev/null || echo "nvidia-smi failed"
            echo ""
            echo "# --query-gpu (per-GPU summary)"
            nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu \
                --format=csv 2>/dev/null || true
            echo ""
            echo "# --query-compute-apps (active processes)"
            nvidia-smi --query-compute-apps=pid,process_name,used_memory \
                --format=csv 2>/dev/null || true
        else
            echo "nvidia-smi не установлен"
        fi
        echo '```'
        echo ""

        echo "## free -h (host RAM/swap; на DGX Spark unified-memory ≈ VRAM)"
        echo ""
        echo '```'
        free -h 2>/dev/null || echo "free недоступен"
        echo '```'
        echo ""

        echo "## docker stats --no-stream"
        echo ""
        echo '```'
        if command -v docker >/dev/null 2>&1; then
            if docker info >/dev/null 2>&1; then
                docker stats --no-stream \
                    --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" \
                    2>/dev/null || echo "docker stats failed"
            else
                echo "docker daemon недоступен"
            fi
        else
            echo "docker не установлен"
        fi
        echo '```'
        echo ""

        echo "## docker images (все теги стека)"
        echo ""
        echo '```'
        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            docker images --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}" \
                2>/dev/null | head -60 \
                || echo "docker images failed"
        else
            echo "docker недоступен"
        fi
        echo '```'
        echo ""

        echo "## du -sh — модели и кэши"
        echo ""
        echo '```'
        for path in \
            "$LMSTUDIO_MODELS_DIR" \
            "$LMSTUDIO_MODELS_DIR/CCSSNE" \
            "$HOME/.lmstudio/models" \
            "$HOME/.lmstudio/models/CCSSNE"; do
            if [ -e "$path" ]; then
                du -sh "$path" 2>/dev/null || true
            fi
        done

        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            for vol in \
                localai-models-volume \
                localai-data-volume \
                speaches-hf-volume \
                openedai-speech-voices-volume \
                kokoro-cache-volume \
                openwebui-data-volume \
                phoenix-pg-volume \
                litellm-pg-volume \
                phoenix-data-volume; do
                mp="$(docker volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null || true)"
                if [ -n "$mp" ] && [ -d "$mp" ]; then
                    sudo_du="$(sudo -n du -sh "$mp" 2>/dev/null || true)"
                    if [ -n "$sudo_du" ]; then
                        printf '%s  (%s)\n' "$sudo_du" "$vol"
                    else
                        # Fallback: ask docker (no sudo) via a one-shot busybox.
                        sz="$(docker run --rm -v "$vol":/v alpine sh -c 'du -sh /v 2>/dev/null' 2>/dev/null || true)"
                        if [ -n "$sz" ]; then
                            printf '%s  (%s, via container)\n' "$sz" "$vol"
                        fi
                    fi
                fi
            done
        fi
        echo '```'
        echo ""

        echo "## Сводная таблица (ожидаемые порядки vs снимки)"
        echo ""
        cat <<'TABLE'
| Компонент            | RAM (ожид.) | VRAM (ожид.) | Disk (ожид.) |
| -------------------- | ----------- | ------------ | ------------ |
| Qwen3.6-35B (llama)  | ~1 GB       | ~70 GB       | ~70 GB       |
| faster-whisper-v3    | ~1 GB       | ~3.5 GB      | ~3 GB        |
| openedai-speech XTTS | ~1.5 GB     | ~4.5 GB      | ~2 GB        |
| Kokoro-FastAPI       | ~0.5 GB     | ~1.5 GB      | ~0.7 GB      |
| LocalAI (vad-silero) | ~0.3 GB     | ~0.02 GB     | ~0.05 GB     |
| OpenWebUI            | ~0.4 GB     | —            | —            |
| LiteLLM              | ~0.2 GB     | —            | —            |
| SearXNG + Valkey     | ~0.3 GB     | —            | —            |
| OpenHands (idle)     | ~0.6 GB     | —            | ~5 GB        |
| Claw Code (idle)     | ~0.2 GB     | —            | ~0.5 GB      |
TABLE
        echo ""
        echo 'Фактические значения — в `docker stats --no-stream` и `du -sh` выше.'
    } >"$target"
}

case "$MODE" in
    snapshot|baseline)
        snapshot "$OUTPUT"
        ok "Отчёт сохранён в $OUTPUT"
        ;;
    diff)
        if [ ! -f "$BASELINE_DEFAULT" ]; then
            warn "Нет baseline-снимка ($BASELINE_DEFAULT). Сначала: bash scripts/memory-report.sh --baseline"
            exit 1
        fi
        AFTER="$(mktemp)"
        snapshot "$AFTER"
        {
            echo "# Memory diff — $TS"
            echo ""
            echo "BEFORE: \`$BASELINE_DEFAULT\`"
            echo "AFTER:  \`$OUTPUT\`"
            echo ""
            echo '```diff'
            diff -u "$BASELINE_DEFAULT" "$AFTER" || true
            echo '```'
        } >"$OUTPUT"
        rm -f "$AFTER"
        ok "Diff сохранён в $OUTPUT"
        ;;
esac
