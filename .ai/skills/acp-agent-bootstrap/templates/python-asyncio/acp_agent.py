#!/usr/bin/env python3
"""Minimal ACP-compliant agent — asyncio + stdlib only.

Implements the must-have Agent methods so it passes a smoke handshake:

    printf '%s\n' \\
      '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"terminal":true},"clientInfo":{"name":"smoke","version":"1.0"}}}' \\
      '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}' \\
      '{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"REPLACE_AFTER_NEW","prompt":[{"type":"text","text":"hello"}]}}' \\
      | python3 acp_agent.py

Replace `_handle_prompt` with your real LLM call (LiteLLM, OpenAI, etc.).
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any, AsyncIterator

PROTOCOL_VERSION = 1
AGENT_INFO = {"name": "minimal-acp-agent", "version": "0.1.0"}
AGENT_CAPABILITIES = {
    "loadSession": False,
    "mcpCapabilities":    {"http": True, "sse": True},
    "promptCapabilities": {"embeddedContext": True, "image": False},
    "sessionCapabilities": {"close": {}},
}


def log(*args: Any) -> None:
    """Use stderr for diagnostics — stdout is reserved for NDJSON wire."""
    print("[acp-agent]", *args, file=sys.stderr, flush=True)


class JsonRpcStdioPeer:
    """Bi-directional JSON-RPC 2.0 peer over NDJSON on stdio.

    Lift this class as-is for any new ACP agent; it has no
    ACP-specific knowledge.
    """

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._stdout = sys.stdout.buffer
        self._closed = asyncio.Event()

    async def _send(self, envelope: dict[str, Any]) -> None:
        line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._stdout.write(line)
            self._stdout.flush()

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            return await fut
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def respond(self, req_id: Any, result: Any) -> None:
        await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def respond_error(self, req_id: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        await self._send({"jsonrpc": "2.0", "id": req_id, "error": err})

    async def reader_loop(self, handler) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    self._closed.set()
                    return
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log("dropped non-JSON line:", exc)
                    continue
                # Route: response vs request/notification
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.get(int(msg["id"]))
                    if fut and not fut.done():
                        if "error" in msg:
                            err = msg["error"] or {}
                            fut.set_exception(RuntimeError(
                                f"peer error {err.get('code')}: {err.get('message')}"))
                        else:
                            fut.set_result(msg.get("result"))
                    continue
                asyncio.create_task(handler(msg))
        except Exception as exc:
            log("reader_loop crashed:", exc)
            self._closed.set()


class AcpAgent:
    """Stateful ACP agent — one initialize, many sessions."""

    def __init__(self) -> None:
        self.peer = JsonRpcStdioPeer()
        self._initialized = False
        self._sessions: dict[str, dict[str, Any]] = {}
        self._cancel: dict[str, asyncio.Event] = {}

    # ── dispatch ────────────────────────────────────────────────────────────
    async def handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        req_id = msg.get("id")

        if req_id is None:
            # Notification — no response expected.
            await self._handle_notification(method, params)
            return

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "session/new":
                result = await self._handle_session_new(params)
            elif method == "session/prompt":
                result = await self._handle_session_prompt(params)
            elif method == "session/set_mode":
                result = await self._handle_session_set_mode(params)
            else:
                await self.peer.respond_error(req_id, -32601,
                                              f"method not found: {method}")
                return
            await self.peer.respond(req_id, result)
        except _AcpInvalidParams as exc:
            await self.peer.respond_error(req_id, -32602, "Invalid params", exc.data)
        except Exception as exc:
            log("handler crash on", method, ":", exc)
            await self.peer.respond_error(req_id, -32603, f"internal error: {exc}")

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "session/cancel":
            sid = params.get("sessionId")
            ev = self._cancel.get(sid)
            if ev:
                ev.set()
                log("cancel requested for", sid)

    # ── method handlers ─────────────────────────────────────────────────────
    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_version = int(params.get("protocolVersion") or 0)
        if client_version < 1:
            raise _AcpInvalidParams({"protocolVersion": {"_errors":
                ["expected >= 1, got " + repr(client_version)]}})
        self._client_caps = params.get("clientCapabilities") or {}
        self._initialized = True
        return {
            "protocolVersion": min(client_version, PROTOCOL_VERSION),
            "agentCapabilities": AGENT_CAPABILITIES,
            "authMethods": [],
            "agentInfo": AGENT_INFO,
        }

    async def _handle_session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            raise _AcpInvalidParams({"_errors": ["initialize not called"]})
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd.startswith("/"):
            raise _AcpInvalidParams({"cwd": {"_errors": ["must be absolute path"]}})
        mcp_servers = params.get("mcpServers") or []
        if not isinstance(mcp_servers, list):
            raise _AcpInvalidParams({"mcpServers": {"_errors": ["must be array"]}})
        sid = "ses_" + uuid.uuid4().hex[:24]
        self._sessions[sid] = {"cwd": cwd, "mcp_servers": mcp_servers, "mode": "build"}
        self._cancel[sid] = asyncio.Event()
        log("session/new →", sid, "cwd=", cwd, "mcp=", len(mcp_servers))
        return {"sessionId": sid}

    async def _handle_session_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        if sid not in self._sessions:
            raise _AcpInvalidParams({"sessionId": {"_errors": ["unknown session"]}})
        self._sessions[sid]["mode"] = str(params.get("modeId") or "build")
        await self.peer.notify("session/update", {
            "sessionId": sid,
            "update": {"sessionUpdate": "current_mode_update",
                       "currentModeId": self._sessions[sid]["mode"]},
        })
        return {}

    async def _handle_session_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("sessionId")
        if sid not in self._sessions:
            raise _AcpInvalidParams({"sessionId": {"_errors": ["unknown session"]}})
        prompt_blocks = params.get("prompt") or []
        text_in = " ".join(b.get("text", "") for b in prompt_blocks
                           if isinstance(b, dict) and b.get("type") == "text")
        cancel_event = self._cancel[sid]
        cancel_event.clear()

        # ── REPLACE THIS BLOCK WITH YOUR LLM CALL ────────────────────────
        # Demo: stream back the input wrapped in a friendly reply.
        async for chunk in self._fake_stream(f"echo: {text_in.strip() or '(empty)'}"):
            if cancel_event.is_set():
                return {"stopReason": "cancelled"}
            await self.peer.notify("session/update", {
                "sessionId": sid,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": chunk}},
            })
        # ─────────────────────────────────────────────────────────────────

        return {"stopReason": "end_turn"}

    async def _fake_stream(self, text: str) -> AsyncIterator[str]:
        for word in text.split():
            await asyncio.sleep(0.02)
            yield word + " "


class _AcpInvalidParams(Exception):
    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__("invalid params")
        self.data = data


async def main() -> None:
    agent = AcpAgent()
    log("ready. waiting for initialize on stdin (NDJSON)…")
    await agent.peer.reader_loop(agent.handle)
    log("stdin closed, exiting.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
