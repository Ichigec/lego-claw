# shellbox: minimal sandbox for the OpenWebUI CLI tool integration.
#
# Layout:
#   - Python 3.12 slim base.
#   - mcpo (open-webui/mcpo) wraps an MCP server as an OpenAPI service.
#   - mcp-shell-server (tumf/mcp-shell-server) provides the actual CLI tool;
#     it enforces an ALLOW_COMMANDS whitelist out of the box.
#   - Non-root user `runner`, no shell history, /workspace mounted read-only at
#     compose-time.
#
# OpenWebUI talks to this container as a Workspace tool URL:
#   http://shellbox:8001/openapi.json
# with header `Authorization: Bearer ${SHELLBOX_API_KEY}`.

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Read-only utilities the LLM is allowed to invoke. Everything here must be
# safe under `read_only: true` + a /workspace ro bind-mount.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        coreutils \
        findutils \
        ripgrep \
        jq \
        curl \
        ca-certificates \
        less \
        procps \
        file \
        gawk \
        sed \
        grep \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# OpenAPI bridge + MCP shell server, both straight from PyPI.
RUN pip install --no-cache-dir \
        "mcpo>=0.0.16" \
        "mcp-shell-server>=1.0.3"

RUN groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid runner --home /home/runner --shell /bin/bash runner \
    && mkdir -p /home/runner /workspace \
    && chown runner:runner /home/runner /workspace

USER runner
WORKDIR /workspace

ENV HOME=/home/runner \
    PATH=/home/runner/.local/bin:/usr/local/bin:/usr/bin:/bin \
    SHELLBOX_PORT=8001

EXPOSE 8001

# `mcpo` listens on $SHELLBOX_PORT and proxies an MCP server (mcp-shell-server,
# stdio) as OpenAPI. The MCP server reads ALLOW_COMMANDS from the environment.
ENTRYPOINT ["/bin/sh", "-c", "exec mcpo --host 0.0.0.0 --port \"$SHELLBOX_PORT\" --api-key \"$MCPO_API_KEY\" -- mcp-shell-server"]
