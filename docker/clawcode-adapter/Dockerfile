# clawcode-adapter — thin HTTP/MCP wrapper that delegates to the Rust `claw`
# CLI inside the existing `clawcode` container via `docker exec`. Two ports
# share one process: FastAPI for the OpenAPI surface (consumed by OpenWebUI as
# a tool server) and an MCP SSE mount (consumed by OpenHands and other LLM
# clients that prefer the MCP transport).
#
# Build context = repo root so we can pull in both `docker/agent-mesh-common/`
# (the shared FastAPI factory) and the adapter-specific server. Mirrors the
# `searchbox` pattern (compose.searchbox.yml uses the same wider context).

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# docker-ce-cli gives us the `docker` binary required to `docker exec` into
# the long-lived `clawcode` container. We do NOT install the daemon — the
# socket is bind-mounted from the host (read-only is not enough for `exec`,
# so RW; the cycle-guard plus bearer auth on the adapter are the mitigation).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        tini \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/clawcode-adapter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY docker/agent-mesh-common/agent_mesh_adapter.py /app/agent_mesh_adapter.py
COPY docker/agent-mesh-common/skills_loader.py /app/skills_loader.py
COPY docker/agent-mesh-common/a2a /app/a2a
COPY docker/clawcode-adapter/server.py /app/server.py

# Generate Python gRPC stubs from the vendored A2A proto (Section 10).
# Failure is non-fatal — the adapter falls back to REST/JSON-RPC only.
RUN bash /app/a2a/proto/regen.sh || \
    echo "warn: proto regen failed; gRPC binding will be disabled at runtime"

ENV LISTEN_PORT=8790 \
    LISTEN_HOST=0.0.0.0 \
    LOG_LEVEL=info \
    CLAWCODE_CONTAINER=clawcode \
    CLAWCODE_ADAPTER_CONCURRENCY=2 \
    MAX_NESTED_AGENT_CALLS=1 \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006

EXPOSE 8790

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "/app/server.py"]
