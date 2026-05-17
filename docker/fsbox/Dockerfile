# fsbox: filesystem MCP sandbox for the agent_dev workspace.
#
# Layout:
#   - Node 22 slim (needed for @modelcontextprotocol/server-filesystem).
#   - Python 3 + pip for `mcpo` (the OpenAPI bridge in front of any MCP server).
#   - mcp-server-filesystem (typed FS API: read_file, write_file, edit_file,
#     list_directory, search_files, move_file, create_directory).
#   - User uid 1000 / gid 1000 → matches the first non-root host user, so
#     files written into $FSBOX_WORKSPACE_DIR (default ~/agent_dev) keep the
#     expected ownership for the IDE. Override uid/gid in compose.fsbox.yml
#     via FSBOX_USER_UID/FSBOX_USER_GID if your host user has different ids.
#
# OpenWebUI talks to this container over llm-stack-net:
#   http://fsbox:8001/openapi.json
# with header `Authorization: Bearer ${FSBOX_API_KEY}`.

FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        ca-certificates \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# OpenAPI bridge for any MCP server, straight from PyPI.
# `--break-system-packages` is required on Debian Bookworm (PEP 668).
RUN pip install --no-cache-dir --break-system-packages "mcpo>=0.0.16"

# Pre-install the filesystem MCP server globally; this avoids npx network
# fetches at container start and lets us run with `read_only: true`.
RUN npm install -g @modelcontextprotocol/server-filesystem@latest \
    && npm cache clean --force

# node:22-slim ships a `node` user at uid 1000 already. Rename it to `agent`
# so logs / `ps` output read clearly, and reuse the same uid:gid.
RUN userdel -r node 2>/dev/null || true \
    && groupadd --gid 1000 agent \
    && useradd --uid 1000 --gid agent \
        --home /home/agent --shell /bin/bash agent \
    && mkdir -p /home/agent /workspace \
    && chown -R agent:agent /home/agent /workspace

USER agent
WORKDIR /workspace

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin \
    FSBOX_PORT=8001

EXPOSE 8001

# `mcpo` listens on $FSBOX_PORT and proxies the MCP filesystem server (stdio)
# as an OpenAPI service. The MCP server is rooted at /workspace, which is the
# bind-mount of $FSBOX_WORKSPACE_DIR (default ~/agent_dev, rw) — see
# compose.fsbox.yml.
ENTRYPOINT ["/bin/sh", "-c", "exec mcpo --host 0.0.0.0 --port \"$FSBOX_PORT\" --api-key \"$MCPO_API_KEY\" -- mcp-server-filesystem /workspace"]
