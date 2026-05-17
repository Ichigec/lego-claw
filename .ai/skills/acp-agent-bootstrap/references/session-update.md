# `session/update` notifications

Streaming updates that the **Agent** pushes to the **Client** during a
`session/prompt` turn. All are JSON-RPC **notifications** (no `id`,
no response).

```jsonc
{
  "jsonrpc": "2.0",
  "method":  "session/update",
  "params": {
    "sessionId": "ses_…",
    "update": { /* one of the variants below */ }
  }
}
```

The discriminator is `update.sessionUpdate` (or the legacy/idiomatic
shorter form `update.kind` in some SDKs — opencode uses `sessionUpdate`).

## Variants

### `agent_message_chunk` — incremental Markdown text

```jsonc
{
  "sessionUpdate": "agent_message_chunk",
  "content": { "type": "text", "text": "partial token text" }
}
```

Send one per ~chunk of streamed model output. Concatenate on the Client
to assemble the full message.

### `agent_thought_chunk` — chain-of-thought (optional)

Same shape as `agent_message_chunk` but rendered separately by Clients
that support a "thinking" pane (Zed, opencode-tui).

### `tool_call` — announce a tool invocation

```jsonc
{
  "sessionUpdate": "tool_call",
  "toolCallId":    "tc_42",
  "title":         "Read foo.py",
  "kind":          "read",          // read | edit | move | delete | create | execute | think | search | fetch | other
  "status":        "in_progress",   // in_progress | completed | failed
  "rawInput":      { "path": "/workspace/project/foo.py" },
  "locations":     [{ "path": "/workspace/project/foo.py" }]
}
```

### `tool_call_update` — mutate an in-flight tool call

Only fields you change need to be in the payload — `toolCallId` is the
PK.

```jsonc
{
  "sessionUpdate": "tool_call_update",
  "toolCallId":    "tc_42",
  "status":        "completed",
  "rawOutput":     { "linesRead": 120 },
  "content":       [{ "type": "text", "text": "...truncated diff..." }]
}
```

`kind` of `edit | move | delete | create` should trigger the
"file changed" indicator on the Client. Our `opencode-adapter` uses
this to populate `files_changed` in the run result.

### `plan` — upfront plan with progress

```jsonc
{
  "sessionUpdate": "plan",
  "entries": [
    { "content": "Read foo.py",       "priority": "high",   "status": "completed"   },
    { "content": "Run pytest",        "priority": "medium", "status": "in_progress" },
    { "content": "Open PR",           "priority": "low",    "status": "pending"     }
  ]
}
```

Send once with all `pending` then re-send with status mutations.

### `available_commands_update` — slash commands

```jsonc
{
  "sessionUpdate": "available_commands_update",
  "availableCommands": [
    { "name": "init",    "description": "guided AGENTS.md setup" },
    { "name": "compact", "description": "compact the session" }
  ]
}
```

opencode emits this right after `session/new`. Clients render them as
`/init`, `/compact` slash commands.

### `current_mode_update` — mode changed

```jsonc
{ "sessionUpdate": "current_mode_update", "currentModeId": "plan" }
```

Mirrors a `session/set_mode` from the Client side.

### `user_message_chunk` — Client → Client echo (rare)

Used when the Agent injects synthetic user turns (multi-step planners).
Same shape as `agent_message_chunk` with `"role": "user"`.

## Ordering and atomicity

- Updates are **strictly ordered per-session**. The Client will render
  them in arrival order.
- A turn looks like: `[tool_call] [tool_call_update]* [agent_message_chunk]+`
  repeated, ending with the `session/prompt` response.
- Do not send updates for sessions you have not opened (`sessionId`
  unknown) — clients drop them silently.

## How `opencode-adapter` consumes these

[`docker/opencode-adapter/acp_client.py`](docker/opencode-adapter/acp_client.py)
puts every update into a per-session `asyncio.Queue`. The adapter then:

- aggregates `agent_message_chunk.content.text` → `RunResult.result_text`
- collects `tool_call`/`tool_call_update` with `kind ∈ {edit,create,delete,move}`
  → `RunResult.files_changed`
- writes a short tail (last N updates) → `RunResult.logs_tail`

That same dispatch pattern works for any new Client.
