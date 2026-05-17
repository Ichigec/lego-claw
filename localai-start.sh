#!/usr/bin/env bash
# Trial launch script for LocalAI.
# Run from the project root: bash localai-start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.localai.yml"
COMPOSE_FILE_QWEN="$SCRIPT_DIR/compose.localai.qwen36.yml"
LOCALAI_HOST_PORT="${LOCALAI_HOST_PORT:-8180}"
LOCALAI_ENABLE_QWEN36="${LOCALAI_ENABLE_QWEN36:-0}"
# Skip the audio-backend bootstrap step (whisper / faster-qwen3-tts / fish-speech /
# piper / silero-vad). Set to 1 for fast iteration on compose without re-pulling
# multi-GB OCI plugin images.
LOCALAI_SKIP_BACKENDS="${LOCALAI_SKIP_BACKENDS:-0}"

echo "=== LocalAI – пробный запуск ==="
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    echo "→ Включен legacy-профиль qwen3.6-35b-heretic"
fi

# ── 0. Pick the correct image for this CPU architecture ────────────────────
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ]; then
    # Detect CUDA major version (DGX Spark = 13, Jetson AGX Orin = 12)
    CUDA_MAJOR="$(nvidia-smi 2>/dev/null | grep -oP 'CUDA Version: \K[0-9]+' | head -1 || echo 12)"
    if [ "$CUDA_MAJOR" -ge 13 ]; then
        export LOCALAI_IMAGE="localai/localai:latest-nvidia-l4t-arm64-cuda-13"
    else
        export LOCALAI_IMAGE="localai/localai:latest-nvidia-l4t-arm64"
    fi
    echo "→ ARM64 + CUDA $CUDA_MAJOR → образ: $LOCALAI_IMAGE"
else
    # x86-64: honour existing env or keep compose default
    export LOCALAI_IMAGE="${LOCALAI_IMAGE:-localai/localai:latest-gpu-nvidia-cuda-12}"
    echo "→ x86-64 → образ: $LOCALAI_IMAGE"
fi

# ── 1. Add LocalAI variables to .env if not already present ────────────────
if ! grep -q "LOCALAI_HOST_PORT" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "→ Добавляю LocalAI-переменные в $ENV_FILE …"
    sudo tee -a "$ENV_FILE" > /dev/null <<ENVBLOCK

# ─── LocalAI ─────────────────────────────────────────────────────────────────
LOCALAI_HOST_PORT=8180
LOCALAI_CONTEXT_SIZE=8192
LMSTUDIO_MODELS_DIR=\${HOME}/.lm-studio/models
LOCALAI_IMAGE=${LOCALAI_IMAGE}
ENVBLOCK
    echo "   ✓ переменные добавлены"
else
    echo "→ LocalAI-переменные в .env уже присутствуют — пропускаю"
fi

# ── 2. Ensure the user can talk to Docker (add to group if needed) ──────────
if ! docker info &>/dev/null; then
    echo ""
    echo "→ Пользователь не в группе docker — добавляю …"
    sudo usermod -aG docker "$USER"
    echo "   Группа добавлена. Перезапускаю скрипт через newgrp docker …"
    exec sg docker -c "bash '$BASH_SOURCE' --skip-group-check"
fi

# ── 3. Ensure network llm-stack-net exists ──────────────────────────────────
if ! docker network inspect llm-stack-net &>/dev/null; then
    echo ""
    echo "→ Сеть llm-stack-net не найдена."
    echo "  Если основной стек не запущен, создаю сеть вручную …"
    docker network create llm-stack-net
    echo "   ✓ сеть создана"
else
    echo "→ Сеть llm-stack-net существует — ОК"
fi

# ── 4. Start LocalAI ────────────────────────────────────────────────────────
echo ""
echo "→ Запускаю LocalAI …"
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$COMPOSE_FILE_QWEN" up -d --pull missing
else
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --pull missing
fi

# ── 5. Wait for healthcheck ─────────────────────────────────────────────────
echo ""
echo "→ Ожидаю готовности контейнера (healthcheck /readyz) …"
TIMEOUT=120
ELAPSED=0
until docker inspect --format='{{.State.Health.Status}}' localai 2>/dev/null | grep -q "healthy"; do
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        echo ""
        echo "⚠  Контейнер не стал healthy за ${TIMEOUT}s — проверьте логи:"
        echo "   docker logs localai --tail 40"
        exit 1
    fi
    printf "."
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done
echo ""
echo "   ✓ LocalAI готов"
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    if curl -fsS "http://localhost:$LOCALAI_HOST_PORT/v1/models" | grep -q '"id":"qwen3.6-35b-heretic"'; then
        echo "   ✓ qwen3.6-35b-heretic зарегистрирован в LocalAI"
    else
        echo "   ⚠ qwen3.6-35b-heretic пока не появился в /v1/models"
    fi
fi

# ── 5.5 Bootstrap audio backends ────────────────────────────────────────────
# В LocalAI v4 backends — отдельные OCI-plugin образы, ставятся через gallery
# (POST /backends/apply). Они оседают в localai-models-volume, но при
# `docker compose down -v` пропадут. Этот блок идемпотентен: если backend уже
# стоит в /backends — пропускает. Управляется LOCALAI_SKIP_BACKENDS=1.
LOCALAI_BASE="http://localhost:$LOCALAI_HOST_PORT"
# Формат записи: "<gallery-id>|<runtime-name-в-GET /backends>"
BACKENDS_TO_INSTALL=(
    # STT — два варианта: whisper.cpp CUDA (по умолчанию OpenWebUI),
    # faster-whisper CT2 (резервный для борьбы с галлюцинациями на коротких
    # фразах), qwen-asr (опциональный multilingual ASR, медленный но точнее
    # на сложных RU-фразах с custom vocab).
    "localai@cuda13-nvidia-l4t-arm64-whisper|whisper"
    "localai@nvidia-l4t-arm64-faster-whisper|faster-whisper"
    "localai@cuda13-nvidia-l4t-arm64-qwen-asr|qwen-asr"
    # TTS — два GPU-кандидата под RU + ARM64+CUDA-13.
    # qwen-tts: основной backend для Qwen3-TTS-CustomVoice (1.7B / 0.6B).
    # faster-qwen3-tts: realtime CUDA-graph вариант, требует своих регистраций.
    # fish-speech: multilingual GPU TTS (RU/EN/ZH/JA/…), voice-cloning.
    "localai@cuda13-nvidia-l4t-arm64-qwen-tts|qwen-tts"
    "localai@cuda13-nvidia-l4t-arm64-faster-qwen3-tts|faster-qwen3-tts"
    "localai@cuda13-nvidia-l4t-arm64-fish-speech|fish-speech"
    # CPU fallbacks — robot but instant; gallery image is universal.
    "localai@piper|piper"
    "localai@silero-vad|silero-vad"
)

wait_for_backend_job() {
    local job_uuid="$1" name="$2"
    local deadline=$((SECONDS + 1800))
    while [ "$SECONDS" -lt "$deadline" ]; do
        local body processed error msg
        body="$(curl -fsS "$LOCALAI_BASE/backends/jobs/$job_uuid" 2>/dev/null || true)"
        if [ -n "$body" ]; then
            processed="$(echo "$body" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("processed",False))' 2>/dev/null || echo False)"
            if [ "$processed" = "True" ]; then
                error="$(echo "$body" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("error") or "")' 2>/dev/null || echo "")"
                if [ -z "$error" ]; then
                    echo "   ✓ $name установлен"
                    return 0
                fi
                echo "   ✗ $name: $error"
                return 1
            fi
            msg="$(echo "$body" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("message",""))' 2>/dev/null || echo "")"
            printf "\r   • %s: %s\033[K" "$name" "${msg:0:80}"
        else
            printf "."
        fi
        sleep 5
    done
    echo ""
    echo "   ✗ $name: timeout 30 минут"
    return 1
}

ensure_backends() {
    echo ""
    echo "→ Проверяю audio-backends LocalAI …"
    local installed
    installed="$(curl -fsS "$LOCALAI_BASE/backends" 2>/dev/null \
        | python3 -c 'import json,sys; [print(b.get("Name","")) for b in json.load(sys.stdin)]' 2>/dev/null || true)"

    local entry gallery_id runtime_name resp uuid
    for entry in "${BACKENDS_TO_INSTALL[@]}"; do
        gallery_id="${entry%%|*}"
        runtime_name="${entry##*|}"
        if echo "$installed" | grep -qx "$runtime_name"; then
            echo "   • $runtime_name — уже стоит, пропускаю"
            continue
        fi
        echo "   → ставлю $gallery_id …"
        resp="$(curl -fsS -X POST "$LOCALAI_BASE/backends/apply" \
            -H 'Content-Type: application/json' \
            -d "{\"id\":\"$gallery_id\"}" 2>/dev/null || true)"
        uuid="$(echo "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
        if [ -z "$uuid" ]; then
            echo "   ✗ $gallery_id: apply вернул пустой id ($resp)"
            continue
        fi
        wait_for_backend_job "$uuid" "$runtime_name" || true
        echo ""
    done
}

if [ "$LOCALAI_SKIP_BACKENDS" = "1" ]; then
    echo ""
    echo "→ LOCALAI_SKIP_BACKENDS=1 — bootstrap audio-backends пропущен"
else
    ensure_backends
fi

# ── 6. Summary ──────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo " Web UI:       http://localhost:$LOCALAI_HOST_PORT"
echo " OpenAI API:   http://localhost:$LOCALAI_HOST_PORT/v1"
echo " Talk UI:      http://localhost:$LOCALAI_HOST_PORT/app/talk"
INSTALLED_BACKENDS="$(curl -fsS "http://localhost:$LOCALAI_HOST_PORT/backends" 2>/dev/null \
    | python3 -c 'import json,sys;print(", ".join(b.get("Name","") for b in json.load(sys.stdin)) or "—")' 2>/dev/null || echo "—")"
echo " Backends:     $INSTALLED_BACKENDS"
if [ "$LOCALAI_ENABLE_QWEN36" = "1" ]; then
    echo " Legacy LLM:   qwen3.6-35b-heretic"
    echo " Остановить:   docker compose -f $COMPOSE_FILE -f $COMPOSE_FILE_QWEN down"
else
    echo " Остановить:   docker compose -f $COMPOSE_FILE down"
fi
echo ""
echo "=========================================="
