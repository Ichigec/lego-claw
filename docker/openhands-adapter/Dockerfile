# openhands-adapter — FastAPI + MCP adapter that turns OpenHands 1.6 into a
# delegated agent. Mirror of docker/clawcode-adapter/Dockerfile.
#
# Build context: REPO ROOT.
#   docker build -f docker/openhands-adapter/Dockerfile -t voice-assistant-openhands-adapter:local .
#
# Runtime expectations (see compose.phoenix.yml + docs/agent-mesh.md):
#   - /var/run/docker.sock bind-mounted (RW — adapter spawns `docker run --rm openhands ...`);
#   - The OpenHands GUI's $OPENHANDS_STATE_DIR is bind-mounted from the host
#     so `--resume` works across sessions;
#   - ADAPTER_API_KEY bearer token must match the value OpenWebUI / LiteLLM use.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    LISTEN_PORT=8791 \
    LISTEN_HOST=0.0.0.0 \
    LOG_LEVEL=info \
    OPENHANDS_ADAPTER_CONCURRENCY=1 \
    OPENHANDS_ADAPTER_TIMEOUT=1800 \
    MAX_NESTED_AGENT_CALLS=1 \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/openhands-adapter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt uv \
    && uv tool install openhands --python 3.12 \
    && command -v openhands

COPY docker/agent-mesh-common/agent_mesh_adapter.py /app/agent_mesh_adapter.py
COPY docker/agent-mesh-common/skills_loader.py /app/skills_loader.py
COPY docker/agent-mesh-common/a2a /app/a2a
COPY docker/openhands-adapter/server.py /app/server.py

# Generate Python gRPC stubs from the vendored A2A proto (Section 10).
# Failure is non-fatal — the adapter falls back to REST/JSON-RPC only.
RUN bash /app/a2a/proto/regen.sh || \
    echo "warn: proto regen failed; gRPC binding will be disabled at runtime"

EXPOSE 8791

CMD ["python", "-u", "/app/server.py"]
