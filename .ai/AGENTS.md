# AGENTS.md — global rules for every agent in this repo

This file is **always-on**: every agent (Claw Code, OpenHands, plus any
A2A-compliant runtime that mounts `.ai/` into its workspace) should
prepend it to its system prompt before any per-skill instructions from
`router.md` or `skills/<id>/SKILL.md`.

## 1. Operating envelope

- **Workspace root**: `/workspace/project` inside the agent container.
  Treat that path as the repository root. Stay inside it; never `cd /`
  or write to `/etc`, `/var`, or `$HOME` outside the workspace.
- **Read-only mounts**: `/workspace/project/.ai/` is mounted read-only.
  Do **not** try to edit skill files from a running task — propose a
  diff in chat or use the Skills Manager tool server (HTTP) instead.
- **Networking**: the LLM is reached via `http://host.docker.internal:4000/v1`
  (LiteLLM); MCP servers (`searchbox`, `fsbox`, `shellbox`,
  `clawcode-adapter`, `openhands-adapter`) are reachable on the
  `llm-stack-net` Compose network. Do not invent new endpoints.
- **Cycle guard**: every call carries an `X-Agent-Mesh-Depth` header.
  Refuse to delegate to another agent if `depth >= MAX_NESTED_AGENT_CALLS`.

## 2. House style

- **Languages**: Python 3.12, Bash 5, TypeScript 5+, Rust 1.78+. Match
  the style of files already in the repo. New Python code uses
  `from __future__ import annotations`, type hints, and `pydantic v2`.
- **Comments**: explain *intent and trade-offs*, not what the code
  obviously does. Never narrate the change you are making in a comment.
- **Tests**: prefer `pytest` (Python), `cargo test` (Rust), `bats` for
  shell. If a test framework is not yet wired, add the smallest possible
  config + one passing test instead of inventing a new one.
- **Logging**: use the `logging` module (Python) at INFO level for
  human-visible events, DEBUG for diagnostic detail. Never `print()`
  in production code paths.

## 3. Safety

- Do not commit secrets. Tokens belong in `.env*` and `/run/secrets/*`.
- Do not run `rm -rf` on absolute paths outside the workspace.
- Do not push to `main` directly; create a branch and a PR.
- Network calls to public hosts are allowed for documentation lookups
  (`docs.openhands.dev`, `a2a-protocol.org`, `crates.io`, etc.) but not
  for exfiltrating workspace contents.

## 4. Output discipline

- When asked for a fix, return a unified diff or the full edited file
  contents. Avoid pseudo-code and "you can change X" prose.
- When asked a question, answer the question first, then offer the next
  logical step. Do not bury the lede in 5 paragraphs of context.
- Always cite files using `path/to/file.ext:line` so tool callers can
  jump straight to the source.

## 5. A2A protocol awareness

Every adapter in this repo speaks the A2A protocol on `/a2a/v1/*`
(REST), `/a2a/jsonrpc` (JSON-RPC 2.0), and gRPC on a separate port.
The Agent Card lives at `/.well-known/agent-card.json` and is signed
(JWS, ES256). When a request asks "how does agent X talk to agent Y",
think Agent Card first, Task lifecycle second, transport third — that
is the order the spec puts them in.
