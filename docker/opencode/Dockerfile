# syntax=docker/dockerfile:1.6
#
# opencode (https://opencode.ai/) — terminal-native coding agent that
# speaks the Agent Client Protocol (ACP) natively via `opencode acp`
# (JSON-RPC 2.0 over NDJSON stdio). The container itself stays idle
# (`tail -f /dev/null`) so:
#
#   * `docker exec -it opencode opencode`             → TUI for humans;
#   * `docker exec -i  opencode opencode acp`         → ACP bridge for
#                                                       opencode-adapter;
#   * `docker exec -d  opencode opencode-web-start`   → optional Web UI
#                                                       on :4096 (P3).
#
# Image is tagged voice-assistant-opencode:local from compose.opencode.yml.
# Mirrors docker/clawcode/Dockerfile: non-root user (uid 10102), no
# docker.sock, no nested-runtime. Build is reproducible via the
# OPENCODE_VERSION arg + sha256 pin on the install tarball when set.

ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS runtime

ARG OPENCODE_USER_UID=10102
ARG OPENCODE_USER_GID=10102
# Pin a specific opencode release for supply-chain reproducibility. Empty
# value falls through to the upstream install script's "latest" tag.
ARG OPENCODE_VERSION=
# Optional: pre-install language servers so opencode's LSP/diagnostics
# tools work out of the box. The minimal set is pyright + ts-language-server
# (lightweight, ~80 MB combined); heavier ones (rust-analyzer, gopls) are
# opt-in via build-arg OPENCODE_EXTRA_LSP=rust,go.
ARG OPENCODE_EXTRA_LSP=

ENV DEBIAN_FRONTEND=noninteractive \
    OPENCODE_VERSION=${OPENCODE_VERSION} \
    OPENCODE_HOME=/.opencode \
    # opencode reads OpenAI-compatible env vars (OPENAI_BASE_URL/_API_KEY)
    # — the wrapper in opencode.json maps `litellm` provider to those.
    OPENAI_BASE_URL=http://litellm:4000/v1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        bash \
        tini \
        unzip \
        ripgrep \
        fzf \
        less \
        jq \
        python3 \
        python3-pip \
        python3-venv \
        procps \
        iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${OPENCODE_USER_GID}" opencode \
    && useradd  --uid "${OPENCODE_USER_UID}" --gid opencode \
                --create-home --shell /bin/bash opencode \
    && mkdir -p /workspace/project /.opencode /home/opencode/.config/opencode \
    && chown -R opencode:opencode /workspace /.opencode /home/opencode

# Install opencode itself. The official install script writes the
# binary to ~/.local/bin (per https://opencode.ai/docs/install/), and
# we run it as the unprivileged user so the install footprint stays
# inside HOME.
USER opencode
WORKDIR /home/opencode

RUN set -eux; \
    if [ -n "${OPENCODE_VERSION}" ]; then \
        OPENCODE_VERSION="${OPENCODE_VERSION}" \
            curl -fsSL https://opencode.ai/install | bash ; \
    else \
        curl -fsSL https://opencode.ai/install | bash ; \
    fi

ENV PATH=/home/opencode/.local/bin:/home/opencode/.opencode/bin:${PATH}

# ── P3: pre-install LSP servers (build-arg toggled) ─────────────────────────
# Minimal: pyright (Python) + typescript-language-server (TS/JS) — both
# tiny and useful for opencode's lsp.diagnostics tool. Extra languages
# come in via OPENCODE_EXTRA_LSP=rust,go,ts (csv).
USER root
RUN set -eux; \
    npm install -g --omit=dev \
        pyright@latest \
        typescript-language-server@latest \
        typescript@latest \
        bash-language-server@latest ; \
    case ",${OPENCODE_EXTRA_LSP}," in \
        *,rust,*) \
            apt-get update \
                && apt-get install -y --no-install-recommends rust-src clang \
                && rm -rf /var/lib/apt/lists/* ; \
            # rust-analyzer prebuilt binary (multi-arch as of 2024.05+):
            ARCH="$(uname -m)" ; \
            case "${ARCH}" in \
                x86_64) RA_ARCH=x86_64-unknown-linux-gnu ;; \
                aarch64) RA_ARCH=aarch64-unknown-linux-gnu ;; \
                *) RA_ARCH= ;; \
            esac ; \
            if [ -n "${RA_ARCH}" ]; then \
                curl -fsSL "https://github.com/rust-lang/rust-analyzer/releases/latest/download/rust-analyzer-${RA_ARCH}.gz" \
                    | gunzip -c > /usr/local/bin/rust-analyzer ; \
                chmod 755 /usr/local/bin/rust-analyzer ; \
            fi ;; \
    esac ; \
    case ",${OPENCODE_EXTRA_LSP}," in \
        *,go,*) \
            apt-get update \
                && apt-get install -y --no-install-recommends golang \
                && rm -rf /var/lib/apt/lists/* ; \
            GOBIN=/usr/local/bin go install golang.org/x/tools/gopls@latest ;; \
    esac

USER opencode
WORKDIR /workspace/project

# `tini` reaps zombies; default CMD keeps the container idle so
# subsequent `docker exec` calls (TUI or `opencode acp`) can attach.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["tail", "-f", "/dev/null"]
