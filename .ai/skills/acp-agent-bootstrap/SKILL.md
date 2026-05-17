---
id: acp-agent-bootstrap
name: Build an ACP-compliant agent
description: |
  End-to-end recipe for implementing a new coding agent that speaks the
  Agent Client Protocol (ACP): JSON-RPC 2.0 NDJSON over stdio (local) or
  HTTP/WebSocket (remote). Covers the handshake (initialize), session
  lifecycle (session/new + session/prompt + session/update streaming),
  and the Client-side methods the agent calls back into the editor
  (fs/read_text_file, fs/write_text_file, terminal/*, session/request_permission).
  Source-of-truth spec: https://agentclientprotocol.com/.
version: 0.1.0
tags: [acp, agent, protocol, jsonrpc, stdio, lsp, editor-integration]
agents: [opencode, clawcode, openhands]
triggers:
  - acp
  - agent client protocol
  - поднять acp агента
  - реализовать acp
  - session/prompt
  - session/new
  - json-rpc stdio
  - ndjson
  - zed acp
  - agentclientprotocol.com
inputModes: [text/plain]
outputModes: [text/plain, application/json]
mcp_servers: [searchbox]
examples:
  - "Подними минимальный ACP-агент на Python, который проксирует prompt в OpenAI."
  - "Добавь в наш opencode-adapter поддержку session/set_mode."
  - "Объясни, как ACP handshake отличается от LSP initialize."
securityRequirements: []
---

# Build an ACP-compliant agent

## Purpose

Use this skill when the user wants to **implement an Agent** in the
[Agent Client Protocol](https://agentclientprotocol.com/) sense — the
process the editor (Client) spawns and talks to over JSON-RPC. ACP is
the **LSP-equivalent for coding agents**: one stable wire protocol,
many editors × many agents.

This skill is the dual of `opencode-workflow` (which assumes you are
*using* opencode as an ACP agent from inside our `opencode-adapter`).
Here you are **writing** the agent.

## When to use

- The user says "make an ACP agent", "speak ACP", "session/prompt",
  "agentclientprotocol.com", or names a known ACP editor (Zed, opencode-tui).
- The request mentions stdio JSON-RPC for coding assistants.
- A teammate wants to know how `opencode-adapter` works under the hood.

## Inputs to ask for, if missing

- **Transport** — `stdio` (default) or `http`/`websocket` (remote, WIP).
- **Language/runtime** — Python (asyncio) ships in `templates/python-asyncio/`;
  TypeScript and Rust are equally valid but you'll need to translate.
- **Backing LLM/tooling** — where does the agent get its capabilities?
  (OpenAI API, local LiteLLM, plain echo for the smoke test, etc.)
- **Client capabilities needed** — minimum is none; most agents want
  at least `fs.readTextFile` and `terminal` (the editor exposes those).

## Mental model in 30 seconds

```
┌──────────────┐  initialize  ┌──────────────┐
│   Editor /   │ ───────────▶ │              │
│   Adapter    │              │    Agent     │   ← that's the thing you're
│  (Client)    │ ◀────────── │              │     building
└──────────────┘  session/    └──────────────┘
       ▲          update                ▲
       │ fs/*, terminal/*, request_     │ session/new,
       │ permission (Client methods)    │ session/prompt (Agent methods)
       └────────────────────────────────┘
```

- **One process per agent run.** Editor spawns the agent as a subprocess
  (stdio transport) or opens a connection (remote).
- **Bi-directional JSON-RPC 2.0 over NDJSON.** Both sides can send
  requests, responses, and notifications at any time.
- **One initialize per process. Many sessions per initialize.**
- **One session/prompt per "turn".** Each prompt streams back zero or
  more `session/update` notifications, then a single response with
  `stopReason`.

## Protocol checklist (must-implement vs nice-to-have)

**Must implement on the Agent side:**

- [ ] `initialize` request — return `protocolVersion`, `agentCapabilities`,
      `agentInfo`. Echo back the highest `protocolVersion` you support
      that is ≤ the client's offered one.
- [ ] `session/new` request — open a new session, return `{sessionId}`.
      Accept `cwd` (string, absolute) and `mcpServers` (array, possibly empty).
- [ ] `session/prompt` request — accept `{sessionId, prompt: ContentBlock[]}`,
      stream `session/update` notifications, and respond with `{stopReason}`
      (one of `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`,
      `cancelled`).
- [ ] `session/cancel` notification — best-effort cooperative cancel.

**Nice to have:**

- [ ] `session/set_mode` request — switch built-in "modes" (e.g.
      `build` vs `plan` in opencode).
- [ ] `session/load` request — resume a persisted session (requires
      advertising `agentCapabilities.loadSession=true`).
- [ ] `authenticate` request — used when the agent has its own auth
      methods (`authMethods` returned from `initialize`).

**Client methods you may call (only if `clientCapabilities` permits):**

- `fs/read_text_file`, `fs/write_text_file` — sandboxed I/O via the editor.
- `terminal/create_terminal`, `terminal/output`, `terminal/wait_for_exit`,
  `terminal/release`, `terminal/kill` — long-running shell from the editor.
- `session/request_permission` — interactive approval before doing
  something destructive (write outside workspace, run shell, etc.).

Full list and exact schemas: [references/protocol-overview.md](references/protocol-overview.md),
[references/agent-methods.md](references/agent-methods.md),
[references/client-methods.md](references/client-methods.md),
[references/session-update.md](references/session-update.md).

## Procedure

1. **Choose a transport.** For 99% of cases use **stdio** — it's what
   Zed, opencode-tui, and our `opencode-adapter` expect. HTTP/WebSocket
   is still WIP in the spec.

2. **Frame messages as NDJSON.** One JSON-RPC envelope per line, UTF-8,
   `\n`-terminated. Do *not* use LSP-style `Content-Length` headers.

3. **Spin up the JSON-RPC peer.** Use a library or roll your own:
   - reader loop: `readline()` → dispatch by `id` (response) /
     `method` (request or notification).
   - writer: single async lock around `stdout.write(line + "\n")`.
   - pending-futures map keyed by request id for outgoing calls.
   - Stderr is **free for logging** — the editor will not parse it.

4. **Handshake.** On `initialize`, return the negotiated
   `protocolVersion` (typically `1`), the **subset** of capabilities you
   actually implement, and `agentInfo: {name, version}`. Inspect
   `clientCapabilities` so you know which Client methods you can call.

5. **Sessions.** Maintain `sessionId → state` (cwd, history, MCP
   client handles, mode). Validate `cwd` is absolute and exists. Reject
   `mcpServers` you can't honour with a clear error.

6. **Prompts.** Iterate the user's `ContentBlock[]` (`text`, `image`,
   `resource_link`, …). Stream incremental output via
   `session/update` notifications:
   - `agent_message_chunk` for token-streaming Markdown.
   - `tool_call` + `tool_call_update` for visible tool actions.
   - `plan` for upfront plans.
   - `available_commands_update` for slash commands.

   Finish each turn with a single response containing
   `{stopReason}`. Never close stdout mid-turn.

7. **Permissions before destructive actions.** Send
   `session/request_permission` with a list of `PermissionOption`s and
   await the user's selection (or `cancelled`). Honour
   `allow_always` by caching at the session level.

8. **Cancellation.** When you receive `session/cancel`, set a per-session
   `asyncio.Event` (or equivalent), check it before every LLM/tool call,
   and finish the turn with `stopReason: "cancelled"`.

9. **Smoke test it.** Pipe a hand-crafted NDJSON sequence through
   `your-agent` and check the responses come back in order:

   ```bash
   printf '%s\n' \
     '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},"clientInfo":{"name":"smoke","version":"1.0"}}}' \
     '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}' \
     | python -m my_acp_agent
   ```

   Expect: two JSON responses (id=1 with `protocolVersion`, id=2 with
   `sessionId`), no extra stdout, errors on stderr only.

10. **Plug into an editor.** Point Zed / our `opencode-adapter` /
    your own client at the executable. The Client will exec the
    process and own the lifecycle.

## Reference template

A working minimal Python (asyncio) agent that completes the smoke test
above lives in [templates/python-asyncio/](templates/python-asyncio/).
~150 lines, stdlib only. Copy-paste, swap the `_handle_prompt` body for
your LLM call, done.

## Quality bar

- **Stable wire.** Never write non-JSON to stdout. Log to stderr.
- **Strict schemas.** Reject unknown fields silently? No — return a
  proper JSON-RPC error `-32602 Invalid params` with `data` describing
  the missing/extra fields (opencode does exactly this; see the
  `data._errors` shape in [references/agent-methods.md](references/agent-methods.md)).
- **No global state across sessions.** Every `session/new` returns a
  fresh, isolated id. Two parallel sessions must not see each other's
  history.
- **Honour `clientCapabilities`.** If the client did not advertise
  `terminal`, you must not call `terminal/create_terminal` (the
  client will respond `-32601 Method not found`).

## Anti-patterns

- **Don't** use LSP `Content-Length` framing. ACP is NDJSON.
- **Don't** combine `initialize` and `session/new` into one round-trip.
  They are deliberately separate: `initialize` is per-process,
  `session/new` is per-conversation.
- **Don't** emit unsolicited `session/update` notifications without an
  active prompt turn — they'll be dropped by most clients.
- **Don't** swallow `session/cancel`. Even if you can't interrupt the
  LLM mid-token, you can stop *after* the current chunk.
- **Don't** write the same MCP server entry without the schema-required
  fields (`headers: []` for sse/http, `args: []` for stdio). This is
  the most common `-32602 Invalid params` we hit.

## How this maps to our repo

| Our component | What it is in ACP terms |
| --- | --- |
| `docker/opencode-adapter/acp_client.py` (`AcpClient`) | ACP **Client** — talks to opencode as a sub-process. |
| `docker/opencode-adapter/server.py` (`OpencodeRunner`) | Bridges A2A (REST/JSON-RPC/gRPC) **outside** ↔ ACP **inside**. |
| `opencode` container | ACP **Agent** (third-party, https://opencode.ai/). |
| A new agent you'd build here | ACP **Agent**, would replace opencode. |

Real-world Client implementation to mimic on the Agent side:
[`docker/opencode-adapter/acp_client.py`](docker/opencode-adapter/acp_client.py)
(class `JsonRpcStdioPeer` is a textbook NDJSON peer; lift it as-is).

## Spec lookup

When you need an exact field name or wire example, fetch
`https://agentclientprotocol.com/llms.txt` first (it's a flat
documentation index) and then drill into the specific page.
