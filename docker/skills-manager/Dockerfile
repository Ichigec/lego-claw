# skills-manager — CRUD over the shared `.ai/skills/` tree. The only writer
# of that volume; every successful write fans out a `/admin/reload` to each
# adapter so Agent Card versions bump.
#
# Build context = repo root so we can pull in the shared skills_loader from
# docker/agent-mesh-common/ alongside this service's own code:
#   docker build -f docker/skills-manager/Dockerfile -t voice-assistant-skills-manager:local .
#
# Optional: install the `claude-code-templates` npm CLI to enable the
# `source=aitmpl|claude-templates` import path. Skipped by default to keep
# the image small; set the build-arg `INSTALL_CCT=1` when needed.

ARG INSTALL_CCT=0

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8000 \
    LOG_LEVEL=info \
    SKILLS_DIR=/workspace/.ai/skills

ARG INSTALL_CCT
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini \
    && if [ "$INSTALL_CCT" = "1" ]; then \
        apt-get install -y --no-install-recommends nodejs npm \
        && npm install -g claude-code-templates ; \
    fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/skills-manager/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY docker/agent-mesh-common/skills_loader.py /app/skills_loader.py
COPY docker/skills-manager/server.py /app/server.py

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "/app/server.py"]
