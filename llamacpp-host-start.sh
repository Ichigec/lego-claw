#!/usr/bin/env bash
# Start host-side llama.cpp server for the default Qwen model.
# This intentionally runs outside Docker; LiteLLM reaches it through host.docker.internal.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_OVERRIDE_FILE="${ENV_OVERRIDE_FILE:-$SCRIPT_DIR/.env.llamacpp}"

_LL_ENV_KEYS=(
    LMSTUDIO_MODELS_DIR
    LLAMA_CPP_DIR
    LLAMA_CPP_SERVER_BIN
    LLAMA_CPP_MODEL_PATH
    LLAMA_CPP_MMPROJ_PATH
    LLAMA_CPP_HOST
    LLAMA_CPP_PORT
    LLAMA_CPP_CTX_SIZE
    LLAMA_CPP_GPU_LAYERS
    LLAMA_CPP_THREADS
    LLAMA_CPP_BATCH_SIZE
    LLAMA_CPP_UBATCH_SIZE
    LLAMA_CPP_PARALLEL
    LLAMA_CPP_ALIAS
    LLAMA_CPP_FLASH_ATTN
    LLAMA_CPP_MLOCK
    LLAMA_CPP_MMAP
    LLAMA_CPP_DIRECT_IO
    LLAMA_CPP_KV_UNIFIED
    LLAMA_CPP_N_CPU_MOE
    LLAMA_CPP_JINJA
)

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

load_selected_env "$ENV_FILE" "${_LL_ENV_KEYS[@]}"
load_selected_env "$ENV_OVERRIDE_FILE" "${_LL_ENV_KEYS[@]}"

LMSTUDIO_MODELS_DIR="${LMSTUDIO_MODELS_DIR:-$HOME/.lmstudio/models}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
LLAMA_CPP_SERVER_BIN="${LLAMA_CPP_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"

# Defaults below are taken from the live LM Studio v0.4.12 backend log on this
# host (server-logs/2026-05-01.1.log) — the configuration that hits 677 t/s
# prefill / 16.8 t/s decode on Qwen3.6-35B-A3B-Heretic on GB10/sm_121.
LLAMA_CPP_HOST="${LLAMA_CPP_HOST:-0.0.0.0}"
LLAMA_CPP_PORT="${LLAMA_CPP_PORT:-8090}"
LLAMA_CPP_CTX_SIZE="${LLAMA_CPP_CTX_SIZE:-262144}"
LLAMA_CPP_GPU_LAYERS="${LLAMA_CPP_GPU_LAYERS:-999}"
LLAMA_CPP_THREADS="${LLAMA_CPP_THREADS:-20}"
LLAMA_CPP_BATCH_SIZE="${LLAMA_CPP_BATCH_SIZE:-512}"
LLAMA_CPP_UBATCH_SIZE="${LLAMA_CPP_UBATCH_SIZE:-512}"
LLAMA_CPP_PARALLEL="${LLAMA_CPP_PARALLEL:-4}"
LLAMA_CPP_ALIAS="${LLAMA_CPP_ALIAS:-qwen3.6-35b-heretic}"
LLAMA_CPP_FLASH_ATTN="${LLAMA_CPP_FLASH_ATTN:-on}"
LLAMA_CPP_MLOCK="${LLAMA_CPP_MLOCK:-0}"
LLAMA_CPP_MMAP="${LLAMA_CPP_MMAP:-off}"
LLAMA_CPP_DIRECT_IO="${LLAMA_CPP_DIRECT_IO:-on}"
LLAMA_CPP_KV_UNIFIED="${LLAMA_CPP_KV_UNIFIED:-on}"
LLAMA_CPP_N_CPU_MOE="${LLAMA_CPP_N_CPU_MOE:-0}"
LLAMA_CPP_JINJA="${LLAMA_CPP_JINJA:-on}"

if [ ! -x "$LLAMA_CPP_SERVER_BIN" ]; then
    cat << EOF
llama-server not found: $LLAMA_CPP_SERVER_BIN

Build llama.cpp with CUDA first:

  mkdir -p "$HOME/src"
  git clone https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_DIR"
  rm -rf "$LLAMA_CPP_DIR/build"
  cmake -S "$LLAMA_CPP_DIR" -B "$LLAMA_CPP_DIR/build" \\
    -DGGML_CUDA=ON \\
    -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \\
    -DCMAKE_C_COMPILER=/usr/bin/gcc-12 \\
    -DCMAKE_CXX_COMPILER=/usr/bin/g++-12 \\
    -DCMAKE_CUDA_ARCHITECTURES=121 \\
    -DCMAKE_BUILD_TYPE=Release
  cmake --build "$LLAMA_CPP_DIR/build" --config Release -j"\$(nproc)"

Then run: bash $SCRIPT_DIR/llamacpp-host-start.sh
EOF
    exit 1
fi

if [ -z "${LLAMA_CPP_MODEL_PATH:-}" ]; then
    echo "LLAMA_CPP_MODEL_PATH is not set (add it to .env or .env.llamacpp)" >&2
    exit 1
fi

cmd=(
    "$LLAMA_CPP_SERVER_BIN"
    --model "$LLAMA_CPP_MODEL_PATH"
    --host "$LLAMA_CPP_HOST"
    --port "$LLAMA_CPP_PORT"
    --ctx-size "$LLAMA_CPP_CTX_SIZE"
    --n-gpu-layers "$LLAMA_CPP_GPU_LAYERS"
    --threads "$LLAMA_CPP_THREADS"
    --batch-size "$LLAMA_CPP_BATCH_SIZE"
    --ubatch-size "$LLAMA_CPP_UBATCH_SIZE"
    --parallel "$LLAMA_CPP_PARALLEL"
    --cont-batching
    --alias "$LLAMA_CPP_ALIAS"
)

if [ -n "${LLAMA_CPP_MMPROJ_PATH:-}" ]; then
    cmd+=(--mmproj "$LLAMA_CPP_MMPROJ_PATH")
fi

case "$LLAMA_CPP_FLASH_ATTN" in
    1|true|on|auto) cmd+=(--flash-attn "${LLAMA_CPP_FLASH_ATTN/1/on}") ;;
    0|false|off) cmd+=(--flash-attn off) ;;
    *) echo "Invalid LLAMA_CPP_FLASH_ATTN=$LLAMA_CPP_FLASH_ATTN (use on, off, or auto)" >&2; exit 1 ;;
esac

if [ "$LLAMA_CPP_MLOCK" = "1" ]; then
    cmd+=(--mlock)
fi

# mmap: LM Studio runs with mmap=off + direct-io=on for fastest model load on
# DGX Spark / GB10. Direct I/O implicitly disables mmap inside llama.cpp.
case "$LLAMA_CPP_MMAP" in
    1|true|on)   cmd+=(--mmap) ;;
    0|false|off) cmd+=(--no-mmap) ;;
    auto|default) ;;
    *) echo "Invalid LLAMA_CPP_MMAP=$LLAMA_CPP_MMAP (use on, off, or auto)" >&2; exit 1 ;;
esac

case "$LLAMA_CPP_DIRECT_IO" in
    1|true|on)   cmd+=(--direct-io) ;;
    0|false|off) cmd+=(--no-direct-io) ;;
    auto|default) ;;
    *) echo "Invalid LLAMA_CPP_DIRECT_IO=$LLAMA_CPP_DIRECT_IO (use on, off, or auto)" >&2; exit 1 ;;
esac

case "$LLAMA_CPP_KV_UNIFIED" in
    1|true|on)  cmd+=(--kv-unified) ;;
    0|false|off) cmd+=(--no-kv-unified) ;;
    auto|default) ;;  # leave llama.cpp default in place
    *) echo "Invalid LLAMA_CPP_KV_UNIFIED=$LLAMA_CPP_KV_UNIFIED (use on, off, or auto)" >&2; exit 1 ;;
esac

# Force MoE expert placement.
# LLAMA_CPP_N_CPU_MOE=0  → all experts on GPU (matches LM Studio default).
# LLAMA_CPP_N_CPU_MOE=N  → first N layers' experts on CPU (lower VRAM, slower).
# LLAMA_CPP_N_CPU_MOE=all|cpu → all experts on CPU (--cpu-moe).
# LLAMA_CPP_N_CPU_MOE=auto  → leave llama.cpp default in place.
case "$LLAMA_CPP_N_CPU_MOE" in
    auto|default) ;;
    all|cpu)      cmd+=(--cpu-moe) ;;
    ''|*[!0-9]*)
        echo "Invalid LLAMA_CPP_N_CPU_MOE=$LLAMA_CPP_N_CPU_MOE (use 0, N, all, or auto)" >&2
        exit 1
        ;;
    *) cmd+=(--n-cpu-moe "$LLAMA_CPP_N_CPU_MOE") ;;
esac

# Jinja chat templates: required for tool-use (capabilities: ["tool_use"]) and
# for models whose native chat template includes Jinja control flow.
case "$LLAMA_CPP_JINJA" in
    1|true|on)   cmd+=(--jinja) ;;
    0|false|off) ;;
    *) echo "Invalid LLAMA_CPP_JINJA=$LLAMA_CPP_JINJA (use on or off)" >&2; exit 1 ;;
esac

printf 'Starting llama.cpp server:\n  '
printf '%q ' "${cmd[@]}"
printf '\n\nOpenAI-compatible API: http://localhost:%s/v1\n' "$LLAMA_CPP_PORT"

exec "${cmd[@]}"
