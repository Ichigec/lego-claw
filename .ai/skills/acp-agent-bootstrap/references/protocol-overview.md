# ACP protocol overview

Quick reference distilled from <https://agentclientprotocol.com/>.
Use [agentclientprotocol.com/llms.txt](https://agentclientprotocol.com/llms.txt)
as the canonical index when in doubt.

## Roles

| Role | Who | What it does |
| --- | --- | --- |
| **Client** | The editor / IDE / our `opencode-adapter` | Spawns the agent, owns the user, owns the filesystem and terminal |
| **Agent** | The coding model + tool loop (the thing you are building) | Receives prompts, runs tools, streams output |

Both sides speak JSON-RPC 2.0 and can issue requests, responses, and
notifications **at any time** — it is fully bi-directional.

## Transport

### stdio (default, local agents)

- Agent is spawned as a **child process** of the Client.
- Messages are **NDJSON**: one JSON-RPC envelope per `\n`-terminated
  line, UTF-8.
- **No LSP-style `Content-Length` headers.**
- `stdin` = inbound, `stdout` = outbound, `stderr` = free for logs
  (the Client must not parse it).

### HTTP / WebSocket (remote, work in progress)

The spec acknowledges remote scenarios but the wire format is still
being finalized. Use stdio unless you have a specific reason.

## Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│ Process spawn (Client → exec agent)                              │
├─────────────────────────────────────────────────────────────────┤
│   →  initialize                                                  │
│   ←  initialize result (protocolVersion, agentCapabilities, …)   │
├─────────────────────────────────────────────────────────────────┤
│   →  authenticate          (optional, only if authMethods used)  │
│   ←  authenticate result                                         │
├─────────────────────────────────────────────────────────────────┤
│   →  session/new           (one or more)                         │
│   ←  {sessionId}                                                 │
│                                                                  │
│   For each session — many turns:                                 │
│     →  session/prompt {sessionId, prompt: ContentBlock[]}        │
│     ←  session/update notifications (zero or more)               │
│     ←  session/prompt result {stopReason}                        │
│                                                                  │
│   Cancellation:                                                  │
│     →  session/cancel {sessionId}   (notification, no response)  │
│                                                                  │
│   Mode switch (optional):                                        │
│     →  session/set_mode {sessionId, modeId}                      │
├─────────────────────────────────────────────────────────────────┤
│ stdin closed → agent exits                                       │
└─────────────────────────────────────────────────────────────────┘
```

## Capability negotiation

Both sides advertise what they support at handshake time. **Honour the
other side's advertised set**:

```jsonc
// Client → Agent (initialize.params)
{
  "protocolVersion": 1,
  "clientCapabilities": {
    "fs":       { "readTextFile": true, "writeTextFile": true },
    "terminal": true
  },
  "clientInfo": { "name": "opencode-adapter", "version": "0.1.0" }
}

// Agent → Client (initialize.result)
{
  "protocolVersion": 1,
  "agentCapabilities": {
    "loadSession": true,
    "mcpCapabilities":     { "http": true, "sse": true },
    "promptCapabilities":  { "embeddedContext": true, "image": true },
    "sessionCapabilities": { "close": {}, "fork": {}, "list": {}, "resume": {} }
  },
  "authMethods": [],
  "agentInfo": { "name": "MyAgent", "version": "0.1.0" }
}
```

Implications:

- If `clientCapabilities.terminal` is **absent or false**, the Agent
  must not call any `terminal/*` method.
- If `agentCapabilities.loadSession` is **false**, the Client must not
  call `session/load`.
- `protocolVersion` is **integer**. Today's value is `1`. Echo back
  `min(client_version, your_max)`.

## Error envelope

Use stock JSON-RPC 2.0 codes plus `data` for structured diagnostics:

| Code | Meaning | When to use |
| --- | --- | --- |
| `-32700` | Parse error | Malformed JSON on stdin |
| `-32600` | Invalid request | Missing `jsonrpc`/`method` |
| `-32601` | Method not found | Client called something you don't implement |
| `-32602` | Invalid params | Schema validation failed |
| `-32603` | Internal error | Unhandled exception |
| `-32000..-32099` | Server error | Application-specific (e.g. permission denied) |

opencode's `data` payload for `-32602` is a Zod-style tree:

```json
{
  "code": -32602,
  "message": "Invalid params",
  "data": {
    "_errors": [],
    "url":     { "_errors": ["Invalid input: expected string, received undefined"] },
    "type":    { "_errors": ["Invalid input: expected \"sse\""] }
  }
}
```

Returning the field path tells the Client *exactly* what was wrong;
mimic this shape if you can.

## NDJSON framing — minimum viable

```python
async def writer(stream: asyncio.StreamWriter, envelope: dict) -> None:
    line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode()
    stream.write(line)
    await stream.drain()

async def reader(stream: asyncio.StreamReader) -> AsyncIterator[dict]:
    while True:
        raw = await stream.readline()
        if not raw:
            return  # peer closed stdin
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue  # ignore blank lines
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # log + skip; do NOT crash on garbage
            continue
```

That's the whole transport. Everything else is method dispatch +
session bookkeeping.
