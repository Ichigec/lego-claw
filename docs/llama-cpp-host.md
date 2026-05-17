# Host-side llama.cpp

This setup runs `llama-server` directly on the host and keeps the UI path:

`OpenWebUI -> LiteLLM -> host-side llama.cpp`

OpenWebUI stays pointed at `http://litellm:4000/v1`; it does not talk to
llama.cpp directly. Inside LiteLLM the public alias `qwen3.6-35b-heretic`
is routed to `LLAMA_CPP_API_BASE` (default `http://host.docker.internal:8090/v1`),
which lets it coexist with LM Studio on the same host (see "Wire LiteLLM" below).

## Build llama.cpp

The repo lives in `${HOME}/dev/llama.cpp`. CUDA toolkit on this host is
**13.2** (`/usr/local/cuda-13.2`); GPU is GB10 with compute capability 12.1
(`sm_121`). Use `gcc-12` / `g++-12` — newer GCC versions miscompile some
ggml-cuda kernels on aarch64.

```bash
cd ${HOME}/dev/llama.cpp

git fetch --tags --prune origin
LATEST_TAG=$(git for-each-ref --sort=-creatordate --count=1 \
    --format='%(refname:short)' refs/tags/)
git checkout "$LATEST_TAG"

rm -rf build
cmake -S . -B build \
  -DGGML_CUDA=ON \
  -DGGML_CCACHE=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.2/bin/nvcc \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-12 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-12 \
  -DCMAKE_CUDA_ARCHITECTURES="121-real;121-virtual" \
  -DLLAMA_BUILD_TESTS=OFF
cmake --build build --config Release -j"$(nproc)"
```

Notes:

- Avoid the bare `/usr/local/cuda/bin/nvcc` symlink — there are several CUDA
  installs on this box (`cuda-13.0`, `cuda-13.2`); pin the version explicitly.
- `121-real;121-virtual` embeds both SASS for sm_121 and a PTX fallback, so the
  binary keeps working after future driver updates.
- `GGML_CCACHE=ON` makes subsequent rebuilds incremental (install with
  `apt install ccache`).

After build, sanity-check:

```bash
${HOME}/dev/llama.cpp/build/bin/llama-server --version
# expects:
#   ggml_cuda_init: ... NVIDIA GB10, compute capability 12.1, VMM: yes, VRAM: 124610 MiB
#   built with GNU 12.4.0 for Linux aarch64
```

## Start llama-server

```bash
cd ${HOME}/lego-claw
bash ./llamacpp-host-start.sh
```

The script reads `.env` then `.env.llamacpp` (override). With the current
"Phase A" workaround for the SOFT_MAX crash on GB10, the resulting command is:

```bash
${HOME}/dev/llama.cpp/build/bin/llama-server \
  --model ${HOME}/.lmstudio/models/CCSSNE/tvall43-Qwen3.6-35B-A3B-heretic-gguf/Qwen3.6-35B-A3B-heretic-bf16.gguf \
  --mmproj ${HOME}/.lmstudio/models/CCSSNE/tvall43-Qwen3.6-35B-A3B-heretic-gguf/Qwen3.6-35B-A3B-heretic-mmproj-F32.gguf \
  --host 0.0.0.0 \
  --port 8090 \
  --ctx-size 262144 \
  --n-gpu-layers 40 \
  --threads 20 \
  --batch-size 4090 \
  --parallel 1 \
  --cont-batching \
  --alias qwen3.6-35b-heretic \
  --flash-attn on \
  --kv-unified
```

Tunable knobs in `.env.llamacpp`:

- `LLAMA_CPP_PARALLEL`, `LLAMA_CPP_BATCH_SIZE` — the LM Studio reference is
  `parallel=4`, `batch-size=512`. On older llama.cpp builds this combination
  crashed on warmup with `cudaErrorInvalidValue: SOFT_MAX failed` on GB10;
  retry on every fresh `b8990+` build.
- `LLAMA_CPP_FLASH_ATTN` — `on`/`off`/`auto`. With the SOFT_MAX bug present,
  `off` was the fragile path; current default is `on`.
- `LLAMA_CPP_KV_UNIFIED` — `on` (default), `off`, or `auto`. LM Studio runs
  with Unified KV Cache enabled; this script forces it on regardless of the
  parallel/slots heuristic in upstream llama.cpp.

## Wire LiteLLM

LiteLLM keeps **two independent OpenAI-compatible upstreams**:

- `LMSTUDIO_API_BASE` (default `http://host.docker.internal:1234/v1`) — LM Studio.
- `LLAMA_CPP_API_BASE` (default `http://host.docker.internal:8090/v1`) — this
  host-side llama-server.

Both env vars are injected into the `litellm` container in
`compose.phoenix.yml`. In `docker/litellm/config.yaml` only the public alias
`qwen3.6-35b-heretic` points at `LLAMA_CPP_API_BASE`; everything else
(`tvall43-…`, `huihui-ai_qwen3-coder-…`, `gemma-4-…`, the embeddings model,
etc.) stays on `LMSTUDIO_API_BASE`. So LM Studio and llama-server can run
simultaneously on the same machine.

`.env.llamacpp` controls which upstream `stack-start.sh` health-checks before
bringing up the rest:

```env
# LLM_BACKEND values:
#   both       — both LM Studio (:1234) and llama.cpp (:8090) must respond
#   llamacpp   — only llama.cpp is required
#   lmstudio   — only LM Studio is required (default in .env)
LLM_BACKEND=both

LLAMA_CPP_API_BASE=http://host.docker.internal:8090/v1
LLAMA_CPP_API_KEY=llama-cpp
```

Restart LiteLLM/Phoenix after starting `llama-server`:

```bash
cd ${HOME}/lego-claw
bash ./stack-start.sh
```

If `stack-start.sh` does not recreate LiteLLM (Compose sometimes decides the
config is unchanged), force the recreation:

```bash
cd ${HOME}/lego-claw
set -a; source ./.env; source ./.env.llamacpp; set +a
docker compose --env-file ./.env -f ./compose.phoenix.yml \
    up -d --force-recreate litellm
```

Smoke-test the route:

```bash
cd ${HOME}/lego-claw
bash ./stack-smoke.sh
```

## OpenWebUI

No OpenWebUI change is needed while the public model alias remains
`qwen3.6-35b-heretic`; OpenWebUI discovers and calls it through LiteLLM.

If you ever need to check it in the GUI:

- Chat model: `qwen3.6-35b-heretic`
- OpenAI connection: `http://litellm:4000/v1`
- API key: `sk-local`

Set those under `Admin Panel -> Settings -> Connections` only if the saved
OpenWebUI config was changed manually.
