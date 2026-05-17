# Minimal Python ACP agent

Single-file, stdlib-only, asyncio. ~200 lines including framing.
Passes the canonical handshake smoke test from the spec.

## Files

- `acp_agent.py` — the agent itself. Run with `python3 acp_agent.py`.
- `pyproject.toml` — packaging metadata (optional, for `pip install -e .`).
- `Dockerfile` — minimal image to run the agent over `docker exec -i`.

## Smoke test (no LLM, just the handshake)

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},"clientInfo":{"name":"smoke","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}' \
  | python3 acp_agent.py
```

Expected stdout (two NDJSON lines):

```
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{...},"authMethods":[],"agentInfo":{...}}}
{"jsonrpc":"2.0","id":2,"result":{"sessionId":"ses_<hex>"}}
```

Stderr will carry `[acp-agent] …` log lines — that's expected.

## Full turn test

```bash
# Capture the sessionId from session/new first, then send a prompt:
python3 acp_agent.py <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},"clientInfo":{"name":"smoke","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"PASTE_HERE","prompt":[{"type":"text","text":"hello world"}]}}
EOF
```

You'll see a stream of `session/update` notifications with
`agent_message_chunk` (one per token of the echoed input), then a
final `{"id":3,"result":{"stopReason":"end_turn"}}`.

## Wiring to a real LLM

Replace the comment block in `_handle_session_prompt`:

```python
# ── REPLACE THIS BLOCK WITH YOUR LLM CALL ────────────────────────
async for chunk in self._fake_stream(...):
    ...
# ─────────────────────────────────────────────────────────────────
```

with whatever streaming client you use. For LiteLLM:

```python
import openai
client = openai.AsyncOpenAI(base_url="http://litellm:4000/v1", api_key="sk-local")
stream = await client.chat.completions.create(
    model="qwen3.6-35b-heretic",
    messages=[{"role": "user", "content": text_in}],
    stream=True,
)
async for event in stream:
    if cancel_event.is_set():
        return {"stopReason": "cancelled"}
    delta = (event.choices[0].delta.content or "")
    if delta:
        await self.peer.notify("session/update", {
            "sessionId": sid,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": delta}},
        })
```

## Wiring to our `opencode-adapter`-style host

If you want this agent invokable through our agent-mesh (Track 1
"agent as tool" + Track 2 "LiteLLM A2A"), follow
[`a2a-build-agent`](../../../a2a-build-agent/SKILL.md). The pattern is:

1. Wrap this script in a long-lived container (`Dockerfile` here gives
   you the idle-sidecar pattern).
2. Build an adapter (Python + FastAPI) that does
   `docker exec -i <container> python3 /app/acp_agent.py` and proxies
   `/v1/run` ↔ ACP `session/prompt`.
3. Reuse `agent-mesh-common/agent_mesh_adapter.py` + an `AcpClient`
   identical to `docker/opencode-adapter/acp_client.py` (rename
   container, that's it).

## Limitations of this template

This template is **stdio-only** and does **not** call Client methods
(`fs/*`, `terminal/*`, `session/request_permission`). Add them when
your real agent needs to touch files or shell. See
[`../../references/client-methods.md`](../../references/client-methods.md).
