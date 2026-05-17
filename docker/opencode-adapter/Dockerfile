# opencode-adapter — thin HTTP/MCP wrapper that bridges to the opencode
# CLI's native ACP server (`opencode acp`, JSON-RPC 2.0 over NDJSON stdio)
# via `docker exec -i opencode opencode acp`. Two ports share one process:
# FastAPI for the OpenAPI surface (OpenWebUI tool server) and an MCP SSE
# mount (consumed by Claw / OpenHands and any other LLM client that
# prefers MCP).
#
# Build context = repo root so the Dockerfile can pull in both
# `docker/agent-mesh-common/` (shared FastAPI factory) and the
# adapter-specific server. Mirrors `clawcode-adapter`/`openhands-adapter`.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# docker-ce-cli gives us the `docker` binary required to `docker exec`
# into the long-lived `opencode` container. We do NOT install the
# daemon — the socket is bind-mounted from the host. RW is required for
# exec; bearer auth + cycle-guard + loopback-only publish are the
# compensating controls.
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

COPY docker/opencode-adapter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY docker/agent-mesh-common/agent_mesh_adapter.py /app/agent_mesh_adapter.py
COPY docker/agent-mesh-common/skills_loader.py /app/skills_loader.py
COPY docker/agent-mesh-common/a2a /app/a2a
COPY docker/opencode-adapter/acp_client.py /app/acp_client.py
COPY docker/opencode-adapter/terminals.py /app/terminals.py
COPY docker/opencode-adapter/server.py /app/server.py

# Generate Python gRPC stubs from the vendored A2A proto (Section 10).
# Failure is non-fatal — the adapter falls back to REST/JSON-RPC only.
RUN bash /app/a2a/proto/regen.sh || \
    echo "warn: proto regen failed; gRPC binding will be disabled at runtime"

ENV LISTEN_PORT=8798 \
    LISTEN_HOST=0.0.0.0 \
    LOG_LEVEL=info \
    OPENCODE_CONTAINER=opencode \
    OPENCODE_ADAPTER_CONCURRENCY=2 \
    OPENCODE_ADAPTER_TIMEOUT=1200 \
    OPENCODE_ADAPTER_AUTO_APPROVE=workspace \
    MAX_NESTED_AGENT_CALLS=1 \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006

EXPOSE 8798

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "/app/server.py"]
