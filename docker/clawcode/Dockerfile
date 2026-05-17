# syntax=docker/dockerfile:1.6
#
# Claw Code — clean-room CLI agent (https://claw-code.codes/).
# We pin a specific git ref via build arg CLAWCODE_GIT_REF, install
# Python deps, build the Rust core, and run the agent in non-root mode
# without docker.sock (Claw Code does not have a nested-runtime model).
#
# Image is tagged voice-assistant-clawcode:local from compose.clawcode.yml.

ARG PYTHON_VERSION=3.12

# ── Build stage: clone source + compile Rust core ───────────────────────────
FROM rust:1-slim AS rustbuild
ARG CLAWCODE_GIT_REPO=https://github.com/instructkr/claw-code.git
ARG CLAWCODE_GIT_REF=main
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates pkg-config libssl-dev build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 "${CLAWCODE_GIT_REPO}" /src/claw-code \
    && cd /src/claw-code \
    && git fetch --depth 1 origin "${CLAWCODE_GIT_REF}" \
    && git checkout FETCH_HEAD
WORKDIR /src/claw-code
# Primary product is the Rust `claw` binary (rust/ workspace). `src/main.py`
# is a Python parity/harness — not an interactive agent (requires subcommands).
RUN mkdir -p /artifacts && set -eux; \
    if [ -f rust/Cargo.toml ]; then \
        (cd rust && cargo build --release --locked --workspace) \
            || (cd rust && cargo build --release --workspace); \
        install -m755 rust/target/release/claw /artifacts/claw; \
    elif [ -f Cargo.toml ]; then \
        cargo build --release --locked || cargo build --release; \
        install -m755 target/release/claw /artifacts/claw; \
    elif [ -f core/Cargo.toml ]; then \
        (cd core && (cargo build --release --locked || cargo build --release)); \
        install -m755 core/target/release/claw /artifacts/claw; \
    else \
        echo 'ERROR: no Rust workspace (expected rust/Cargo.toml). Upstream moved layout.' >&2; \
        exit 1; \
    fi

# ── Runtime stage: thin Python image, copy build outputs ────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG CLAWCODE_USER_UID=10101
ARG CLAWCODE_USER_GID=10101
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CLAWCODE_HOME=/.clawcode \
    PYTHONPATH=/app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git tini libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${CLAWCODE_USER_GID}" claw \
    && useradd  --uid "${CLAWCODE_USER_UID}" --gid claw --create-home --shell /bin/bash claw \
    && mkdir -p /workspace/project /.clawcode \
    && chown -R claw:claw /workspace /.clawcode

WORKDIR /app
COPY --from=rustbuild /src/claw-code /app
COPY --from=rustbuild /artifacts/claw /usr/local/bin/claw
RUN chmod 755 /usr/local/bin/claw

# Install Python deps — try both common files, neither is required.
RUN if [ -f requirements.txt ]; then \
        pip install -r requirements.txt ; \
    elif [ -f pyproject.toml ]; then \
        pip install . ; \
    fi \
    && python -c "import sys; print(sys.version)"

USER claw
WORKDIR /workspace/project

# `tini` reaps zombies; default CMD is the interactive CLI entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--"]
# Interactive agent REPL (see upstream USAGE.md). Harness: `python -m src.main --help`.
CMD ["claw"]
