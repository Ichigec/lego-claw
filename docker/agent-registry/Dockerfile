# agent-registry — single A2A entry point that OpenWebUI registers as ONE
# OpenAPI tool server. Discovers JWS-signed Agent Cards from each adapter,
# fans out ListTasks across them, and proxies message:send / :stream /
# :cancel / :subscribe based on `agentId`. See docker/agent-registry/server.py
# for the full contract.
#
# Build context = repo root so the Dockerfile can mirror the existing
# agent-mesh adapters layout (clawcode-adapter, openhands-adapter):
#   docker build -f docker/agent-registry/Dockerfile -t voice-assistant-agent-registry:local .

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8000 \
    LOG_LEVEL=info

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/agent-registry/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY docker/agent-registry/server.py /app/server.py

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "/app/server.py"]
