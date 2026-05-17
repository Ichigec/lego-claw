# Agent methods (Client → Agent)

These are the JSON-RPC methods the editor (Client) calls on **your
Agent**. They are the only entry points you must implement; everything
else is internal.

## `initialize` (request, required)

Called **once** right after the process is spawned. Negotiates protocol
version and capabilities.

```jsonc
// Request
{
  "jsonrpc": "2.0", "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {
      "fs":       { "readTextFile": true, "writeTextFile": true },
      "terminal": true
    },
    "clientInfo": { "name": "editor-name", "version": "x.y.z" }
  }
}

// Response
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentCapabilities": {
      "loadSession": false,
      "mcpCapabilities":    { "http": true, "sse": true },
      "promptCapabilities": { "embeddedContext": true, "image": false },
      "sessionCapabilities":{ "close": {} }
    },
    "authMethods": [],
    "agentInfo": { "name": "MyAgent", "version": "0.1.0" }
  }
}
```

Rules:

- Echo back `min(client_version, your_max)` in `protocolVersion`.
- Advertise the **subset you actually implement**. Lying about
  capabilities triggers bugs that are very hard to debug.
- `authMethods: []` means "no auth needed". If the agent needs an API
  key the user has to type, return `[{id, name, description}]` and
  implement `authenticate`.

## `authenticate` (request, optional)

Only invoked if `initialize.result.authMethods` was non-empty. The
Client passes `{methodId}`; you do whatever your method needs (env
var, interactive prompt via `session/request_permission`, OAuth, …)
and return `{}` on success or an error.

## `session/new` (request, required)

Opens a fresh session. Stateless across `initialize` lifetime — Agent
must maintain the `sessionId → state` map itself.

```jsonc
// Request
{
  "jsonrpc": "2.0", "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/workspace/project",     // absolute, must exist
    "mcpServers": [                  // may be []
      { "name": "searchbox", "type": "sse",
        "url": "http://searchbox:8090/sse", "headers": [] }
    ]
  }
}

// Response
{
  "jsonrpc": "2.0", "id": 2,
  "result": {
    "sessionId": "ses_<opaque>"
    // OPTIONAL extras some agents return:
    // "configOptions": [...],   // user-tunable knobs (model, mode)
    // "models": {...}, "modes": {...}
  }
}
```

`mcpServers` schema is strict — every entry needs the transport-specific
required fields:

| `type` | Required fields |
| --- | --- |
| `"sse"`    | `name`, `url`, `headers` (array of `{name, value}`) |
| `"http"`   | `name`, `url`, `headers` |
| `"stdio"`  | `name`, `command`, `args` (array), `env` (array of `{name, value}`) |

Pass `headers: []` even when empty — `undefined` is rejected.

## `session/load` (request, optional)

Resume a persisted session. Only allowed if `agentCapabilities.loadSession`.

```jsonc
{ "method": "session/load", "params": { "sessionId": "ses_…" } }
```

Return the same shape as `session/new`.

## `session/prompt` (request, required)

The "do work" call. One request = one **turn**.

```jsonc
// Request
{
  "jsonrpc": "2.0", "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "ses_…",
    "prompt": [
      { "type": "text", "text": "Refactor foo.py and run pytest" }
      // also: image, audio, resource_link, resource (ContentBlock union)
    ]
  }
}

// During processing — zero or more `session/update` notifications (see
// references/session-update.md).

// Final response — one and only one:
{
  "jsonrpc": "2.0", "id": 3,
  "result": { "stopReason": "end_turn" }
}
```

Valid `stopReason` values:

| value | meaning |
| --- | --- |
| `end_turn`           | Model finished naturally. |
| `max_tokens`         | Token budget exhausted. |
| `max_turn_requests`  | Tool-call loop hit a built-in cap. |
| `refusal`            | Safety-style refusal. |
| `cancelled`          | Returned in response to `session/cancel`. |

## `session/cancel` (notification, optional but expected)

Cooperative cancel — **notification**, so no response.

```jsonc
{ "method": "session/cancel", "params": { "sessionId": "ses_…" } }
```

Implementation pattern:

```python
self._cancel_events[session_id].set()
# inside the prompt loop:
if self._cancel_events[session_id].is_set():
    return {"stopReason": "cancelled"}
```

## `session/set_mode` (request, optional)

Switch a built-in mode advertised in `session/new.result.modes`
(opencode uses `build` / `plan`).

```jsonc
{ "method": "session/set_mode",
  "params": { "sessionId": "ses_…", "modeId": "plan" } }
```

Respond `{}` on success.

## What you should *not* implement on the Agent side

These are **Client** methods — your Agent calls them, doesn't serve
them:

- `fs/read_text_file`, `fs/write_text_file`
- `terminal/create_terminal`, `terminal/output`, `terminal/release`,
  `terminal/wait_for_exit`, `terminal/kill`
- `session/request_permission`

See [client-methods.md](client-methods.md).
