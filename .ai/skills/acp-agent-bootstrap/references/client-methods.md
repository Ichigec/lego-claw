# Client methods (Agent → Client)

These are JSON-RPC methods the **Agent** calls back into the **Client**.
Your Agent only calls them when the Client advertised the matching
capability in `initialize`.

| Method | Gated by | Purpose |
| --- | --- | --- |
| `fs/read_text_file`           | `clientCapabilities.fs.readTextFile`  | Sandboxed read |
| `fs/write_text_file`          | `clientCapabilities.fs.writeTextFile` | Sandboxed write |
| `terminal/create_terminal`    | `clientCapabilities.terminal`         | Spawn long-running shell |
| `terminal/output`             | `clientCapabilities.terminal`         | Poll stdout/stderr |
| `terminal/wait_for_exit`      | `clientCapabilities.terminal`         | Block until process exits |
| `terminal/release`            | `clientCapabilities.terminal`         | Detach from terminal handle |
| `terminal/kill`               | `clientCapabilities.terminal`         | SIGTERM/SIGKILL the process |
| `session/request_permission`  | always available                      | Ask user before destructive op |

## `fs/read_text_file`

```jsonc
// Request (Agent → Client)
{ "jsonrpc": "2.0", "id": 42,
  "method": "fs/read_text_file",
  "params": {
    "path": "/workspace/project/foo.py",   // absolute
    "line": 10,   // optional, 1-based start line
    "limit": 50   // optional, max lines to return
  }
}

// Response
{ "jsonrpc": "2.0", "id": 42,
  "result": { "content": "...file text..." } }
```

Typical Client behaviour: deny paths outside the open workspace with
JSON-RPC error `-32001` (custom server error code).

## `fs/write_text_file`

```jsonc
{ "method": "fs/write_text_file",
  "params": {
    "path": "/workspace/project/foo.py",
    "content": "...full file content..."
  }
}
```

Returns `{}` on success. The Client may pop up a permission dialog
before doing the write (see `session/request_permission`).

## `terminal/*`

A four-stage handle lifecycle:

1. **Create** — get a handle:
   ```jsonc
   { "method": "terminal/create_terminal",
     "params": {
       "sessionId": "ses_…",
       "command":   "pytest",
       "args":      ["-q"],
       "env":       [],
       "cwd":       "/workspace/project"
     } }
   // → { "result": { "terminalId": "term_…" } }
   ```
2. **Poll** output while it runs:
   ```jsonc
   { "method": "terminal/output",
     "params": { "sessionId": "ses_…", "terminalId": "term_…" } }
   // → { "result": { "stdout": "...", "stderr": "...", "exited": false } }
   ```
3. **Wait** for exit (or `terminal/kill`):
   ```jsonc
   { "method": "terminal/wait_for_exit",
     "params": { "sessionId": "ses_…", "terminalId": "term_…", "timeoutMs": 60000 } }
   // → { "result": { "exitCode": 0, "signaled": false } }
   ```
4. **Release** the handle:
   ```jsonc
   { "method": "terminal/release",
     "params": { "sessionId": "ses_…", "terminalId": "term_…" } }
   ```

Forget to release ⇒ Client may leak the process or refuse to spawn new
ones. Always wrap in try/finally.

## `session/request_permission`

The canonical "ask the user first" call. Used before file writes
outside the workspace, shell commands, network calls, anything
destructive.

```jsonc
// Request
{ "method": "session/request_permission",
  "params": {
    "sessionId": "ses_…",
    "title":      "Run shell command",
    "description":"`rm -rf node_modules`",
    "options": [
      { "id": "allow_once",   "label": "Allow once",        "kind": "allow_once" },
      { "id": "allow_always", "label": "Always allow rm",   "kind": "allow_always" },
      { "id": "deny",         "label": "Deny",              "kind": "reject_once" }
    ]
  } }

// Response
{ "result": { "outcome": { "type": "selected", "optionId": "allow_once" } } }
// or
{ "result": { "outcome": { "type": "cancelled" } } }
```

`kind` is a hint for the Client's UX; the binding semantics live in
the Agent's policy ("if `allow_always` selected, cache decision for
this session/command pair").

## How our `opencode-adapter` serves these

For a battle-tested reference, see
[`docker/opencode-adapter/acp_client.py`](docker/opencode-adapter/acp_client.py):

- `_handle_fs_read` / `_handle_fs_write` — workspace boundary check,
  then plain Python `Path.read_text` / `write_text`.
- `_handle_terminal` — delegates to
  [`docker/opencode-adapter/terminals.py`](docker/opencode-adapter/terminals.py),
  which keeps a `dict[terminalId → asyncio.subprocess.Process]`.
- `_handle_request_permission` — policy-driven (`workspace` /
  `all` / `none` / `ask`), returns `selected` or `cancelled`.

Lift it as-is for any new Client implementation.
