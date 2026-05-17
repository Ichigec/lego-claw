---
id: opencode-workflow
name: opencode workflow (ACP + LSP)
description: |
  Idiomatic workflow for opencode (https://opencode.ai/) when reached via
  the ACP bridge (opencode-adapter). Covers file ops, terminals, LSP
  diagnostics, and the cycle-guard for cross-agent delegation.
version: 0.1.0
tags: [opencode, acp, workflow, lsp, terminal]
agents: [opencode]
triggers:
  - opencode
  - acp
  - lsp
  - diagnostics
  - language server
  - rename symbol
  - find symbol
inputModes: [text/plain]
outputModes: [text/plain, application/json]
mcp_servers: [searchbox]
examples:
  - "Run pyright on the changes and report any errors with line numbers."
  - "Use the terminal to grep for `TODO` markers and group them by file."
  - "Open foo.py via fs/read_text_file, suggest a refactor that keeps tests passing."
securityRequirements: []
---

# opencode workflow (ACP + LSP)

## Purpose

You are running as **opencode** behind the agent-mesh's `opencode-adapter`.
The adapter is the ACP **Client**; it has advertised `fs.readTextFile`,
`fs.writeTextFile`, and `terminal` capabilities, and exposes a configurable
permission policy (`OPENCODE_ADAPTER_AUTO_APPROVE`, default `workspace`).

Use this skill to keep your code edits high-signal, exploit your LSP
tooling, and behave correctly with respect to the cycle-guard.

## When to use

- Any task that touches files inside `/workspace/project` (the shared
  agent sandbox bind-mounted into the container).
- Any task that wants language-aware diagnostics — Python (pyright),
  TS/JS (typescript-language-server) are pre-installed in the image;
  `OPENCODE_EXTRA_LSP=rust,go` adds rust-analyzer/gopls at build time.
- Cross-agent delegation: you may call `clawcode-adapter` or
  `openhands-adapter` as MCP tools, but **never** `opencode-adapter`
  (that would self-loop; the adapter returns HTTP 429).

## Procedure

1. **Plan briefly** before touching files. State the files you intend to
   read, the changes you intend to make, and what verification you will
   run afterwards.
2. **Read before write** via `fs/read_text_file`. Stick to absolute
   paths inside `/workspace/project`; the adapter denies anything else
   with a JSON-RPC error.
3. **Apply minimal edits** with `fs/write_text_file`. Prefer surgical
   diffs to whole-file rewrites; future runs can pick up your changes
   without confusion.
4. **Run diagnostics** before claiming done. For Python:

   ```
   terminal/create cmd="pyright" args=["--outputjson", "<changed-files>"]
   ```

   For TS/JS use `typescript-language-server` (via opencode's built-in
   `find.symbols` / `lsp.diagnostics` tools when possible — they are
   the canonical opencode entry point for language servers).
5. **Tests** — if the project has a Make/`pytest`/`npm test` entry,
   run it via a terminal and surface only the failing summary.
6. **Summarise** in the agent message: list `files_changed`, paste any
   non-trivial diff hunks, and explicitly say "done" when the turn is
   over (the adapter looks for `stopReason: end_turn`).

## Output

A plain text answer suitable for chat clients (OpenWebUI / Claw REPL /
OpenHands UI). Structure it as:

1. **Summary** — 1-2 sentences on what changed and why.
2. **Files changed** — a markdown list of absolute paths.
3. **Verification** — what you ran and what it returned.
4. **Caveats / TODOs** — anything left intentionally for the user.

## Quality bar

- Every edit must be justified by a read (`fs/read_text_file` first or a
  prior `tool_call` with `kind: read`).
- LSP diagnostics must be clean before declaring done; if a diagnostic
  is *intended* (e.g. a known TODO), call it out explicitly.
- No commands run outside `/workspace/project` unless the user
  explicitly authorises it (`OPENCODE_ADAPTER_AUTO_APPROVE=all`).

## Anti-patterns

- **Do not** call `run_opencode` on yourself (cycle guard / 429).
- **Do not** write files outside `/workspace/project` — the adapter
  rejects with `-32001` (`path outside workspace`).
- **Do not** spawn long-running daemons inside a terminal — opencode
  terminals are sized for short, observable commands; for servers use
  the shared `fsbox` / `shellbox` MCP tools instead.
- **Do not** ignore the LSP. Surface every error to the user with line
  number and a one-line explanation; let the user decide whether to
  fix or override.
