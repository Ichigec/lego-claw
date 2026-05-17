"""Minimal async ACP (Agent Client Protocol) client over docker exec stdio.

opencode ships a native ACP bridge via ``opencode acp``: it reads JSON-RPC
2.0 requests/notifications as NDJSON (newline-delimited) from stdin and
writes responses/notifications to stdout. We pipe this stream over
``docker exec -i opencode opencode acp`` and act as the ACP **client**:

* the adapter calls Agent methods ``initialize``, ``session/new``,
  ``session/prompt``, ``session/cancel`` (notification),
  ``session/set_mode``;
* opencode calls Client methods on us:
  ``fs/read_text_file``, ``fs/write_text_file``,
  ``session/request_permission``,
  ``terminal/create``, ``terminal/output``, ``terminal/release``,
  ``terminal/wait_for_exit``, ``terminal/kill``.

Spec reference: https://agentclientprotocol.com/protocol/schema (snake_case
method names; SessionUpdate variants discriminated by ``sessionUpdate``).

Why bespoke async glue instead of the official SDK? The Python SDK is
in flux (mid-2026), pulls a tree of optional deps, and assumes a
``stdin/stdout`` pair under direct control — we have a docker-exec
shaped one. ~400 lines here gives us everything we need with zero
extra dependencies (just stdlib asyncio + json) and stays inside the
slim adapter image.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("opencode-acp-client")


# ── Protocol constants ──────────────────────────────────────────────────────
# opencode targets ACP protocol version 1 (the only version currently
# defined). Bump when upstream updates its schema.
ACP_PROTOCOL_VERSION = 1

# ACP Client capability matrix we advertise to opencode. ``terminal`` is
# a top-level boolean; ``fs.{readTextFile,writeTextFile}`` enable the
# corresponding Client methods.
CLIENT_CAPABILITIES = {
    "fs": {
        "readTextFile": True,
        "writeTextFile": True,
    },
    "terminal": True,
}

# Permission decision strings as defined in the schema:
# RequestPermissionOutcome = {selected, cancelled}; selected wraps an
# optionId chosen from the offered PermissionOption[]. We always pick
# the first ``allow`` / ``allow_always`` option when policy = approve,
# and respond ``cancelled`` when policy = deny.
PERMISSION_POLICY_WORKSPACE = "workspace"
PERMISSION_POLICY_ALL = "all"
PERMISSION_POLICY_NONE = "none"
PERMISSION_POLICY_ASK = "ask"


# ── Public dataclasses ──────────────────────────────────────────────────────
@dataclass
class AcpSessionUpdate:
    """One ``session/update`` notification.

    We keep the raw envelope around (``raw``) so callers can introspect
    fields we don't pre-extract — e.g. ``current_mode_update``, ``plan``.
    """

    session_id: str
    kind: str  # discriminator from sessionUpdate.* variants
    text: str = ""  # extracted text content for chunks
    tool_call_id: str = ""  # populated for tool_call / tool_call_update
    tool_name: str = ""
    tool_kind: str = ""
    tool_status: str = ""
    paths: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AcpPromptResult:
    """Aggregated outcome of one ``session/prompt`` turn."""

    stop_reason: str  # "end_turn" / "max_tokens" / "refusal" / "cancelled" / …
    text: str = ""  # concatenation of agent_message_chunk text content
    thoughts: str = ""  # concatenation of agent_thought_chunk text content
    files_changed: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    updates: list[AcpSessionUpdate] = field(default_factory=list)
    started_at: float = 0.0
    duration_s: float = 0.0


# ── Permission policy helpers ───────────────────────────────────────────────
def _is_under(path: str, root: str) -> bool:
    """True iff *path* resolves to a location under *root* (absolute paths)."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
    except (OSError, RuntimeError):
        return False
    try:
        p.relative_to(r)
    except ValueError:
        return False
    return True


def _collect_paths_from_tool_call(tool_call: dict[str, Any]) -> list[str]:
    """Best-effort extraction of file paths from a ToolCallUpdate payload."""
    paths: list[str] = []
    locations = tool_call.get("locations") or []
    for loc in locations:
        if isinstance(loc, dict):
            path = loc.get("path") or loc.get("uri")
            if isinstance(path, str) and path:
                paths.append(path)
    raw_input = tool_call.get("rawInput") or {}
    if isinstance(raw_input, dict):
        for key in ("path", "filePath", "file_path", "abs_path", "uri"):
            v = raw_input.get(key)
            if isinstance(v, str) and v:
                paths.append(v)
        for key in ("paths", "filePaths", "file_paths"):
            v = raw_input.get(key)
            if isinstance(v, list):
                paths.extend(str(x) for x in v if isinstance(x, str))
    return paths


def decide_permission(
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    policy: str,
    workspace_root: str,
) -> dict[str, Any]:
    """Map our env-driven policy to an ACP RequestPermissionOutcome dict.

    The schema defines two variants:
      * ``{outcome: "selected", optionId: <id>}`` — approve, pick an option;
      * ``{outcome: "cancelled"}``                — deny.
    """
    if not options:
        return {"outcome": "cancelled"}

    policy = (policy or "workspace").strip().lower()

    def _pick(kind: str) -> str:
        """Find the first option matching the desired kind (or its id)."""
        for opt in options:
            if not isinstance(opt, dict):
                continue
            opt_kind = str(opt.get("kind") or "").lower()
            opt_id = str(opt.get("optionId") or opt.get("id") or "")
            if not opt_id:
                continue
            if kind in opt_kind or kind in opt_id.lower():
                return opt_id
        # Fallback: first option, whatever it is.
        for opt in options:
            if isinstance(opt, dict):
                return str(opt.get("optionId") or opt.get("id") or "")
        return ""

    if policy == PERMISSION_POLICY_ALL:
        return {"outcome": "selected", "optionId": _pick("allow") or _pick("once") or _pick("")}

    if policy == PERMISSION_POLICY_NONE:
        return {"outcome": "cancelled"}

    if policy == PERMISSION_POLICY_ASK:
        # No interactive UI in headless mode → conservatively deny.
        return {"outcome": "cancelled"}

    # Default & ``workspace``: approve iff every touched path stays inside
    # the bind-mounted workspace. Deny otherwise.
    paths = _collect_paths_from_tool_call(tool_call)
    if not paths:
        # No paths declared → assume safe (e.g. read-only diagnostics).
        return {"outcome": "selected", "optionId": _pick("allow") or _pick("once") or _pick("")}
    if all(_is_under(p, workspace_root) for p in paths):
        return {"outcome": "selected", "optionId": _pick("allow") or _pick("once") or _pick("")}
    return {"outcome": "cancelled"}


# ── JSON-RPC framing over an asyncio stdio pipe ─────────────────────────────
class JsonRpcStdioPeer:
    """Bidirectional JSON-RPC 2.0 transport with NDJSON framing.

    Async-safe: ``call`` is awaitable and returns the matching response;
    notifications fire-and-forget; incoming requests/notifications are
    dispatched to user-supplied handlers.
    """

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        *,
        request_handler: Callable[[str, dict[str, Any]], Awaitable[Any]],
        notification_handler: Callable[[str, dict[str, Any]], Awaitable[None]],
        on_close: Callable[[], None] | None = None,
        max_line_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._proc = proc
        self._request_handler = request_handler
        self._notification_handler = notification_handler
        self._on_close = on_close
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._closed = asyncio.Event()
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._max_line_bytes = max_line_bytes

    # ── outgoing ────────────────────────────────────────────────────────────
    def _alloc_id(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    async def _send_obj(self, payload: dict[str, Any]) -> None:
        if self._proc.stdin is None or self._proc.stdin.is_closing():
            raise ConnectionError("ACP stdin closed")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._proc.stdin.write(data)
            try:
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise ConnectionError(f"ACP stdin broken: {exc}") from exc

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout_s: float = 60.0) -> Any:
        req_id = self._alloc_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        envelope: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            envelope["params"] = params
        try:
            await self._send_obj(envelope)
            return await asyncio.wait_for(fut, timeout=timeout_s)
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        envelope: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            envelope["params"] = params
        await self._send_obj(envelope)

    async def respond(self, req_id: Any, result: Any) -> None:
        await self._send_obj({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def respond_error(self, req_id: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        await self._send_obj({"jsonrpc": "2.0", "id": req_id, "error": err})

    # ── incoming ────────────────────────────────────────────────────────────
    async def _reader_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                if len(line) > self._max_line_bytes:
                    logger.warning(
                        "ACP NDJSON line too long (%d bytes) — dropping",
                        len(line),
                    )
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError as exc:
                    logger.warning("ACP non-JSON line: %s (err=%s)", line[:120], exc)
                    continue
                if not isinstance(msg, dict):
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ACP reader loop error: %s", exc)
        finally:
            self._closed.set()
            # Fail any pending callers.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("ACP peer closed stdout"))
            self._pending.clear()
            if self._on_close is not None:
                try:
                    self._on_close()
                except Exception:
                    logger.exception("on_close hook failed")

    async def _stderr_loop(self) -> None:
        if self._proc.stderr is None:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.debug("opencode stderr: %s", line.decode("utf-8", errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(int(msg["id"]) if isinstance(msg["id"], (int, str)) and str(msg["id"]).lstrip("-").isdigit() else msg["id"], None)
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg["error"] or {}
                fut.set_exception(AcpError(int(err.get("code", -32000)), str(err.get("message", "")), err.get("data")))
            else:
                fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        if not isinstance(method, str):
            return
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if "id" in msg:
            req_id = msg["id"]
            try:
                result = await self._request_handler(method, params)
            except AcpError as exc:
                await self.respond_error(req_id, exc.code, exc.message, exc.data)
            except Exception as exc:
                logger.exception("ACP request handler raised on %s", method)
                await self.respond_error(req_id, -32603, f"internal error: {exc}")
            else:
                await self.respond(req_id, result if result is not None else {})
        else:
            try:
                await self._notification_handler(method, params)
            except Exception:
                logger.exception("ACP notification handler raised on %s", method)

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def aclose(self) -> None:
        if self._proc.stdin is not None and not self._proc.stdin.is_closing():
            try:
                self._proc.stdin.close()
            except Exception:
                pass
        await self._closed.wait()
        for task in (self._reader_task, self._stderr_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


class AcpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        if data is not None:
            try:
                import json as _json
                rendered = _json.dumps(data, ensure_ascii=False, default=str)[:600]
            except Exception:
                rendered = repr(data)[:600]
            super().__init__(f"ACP error {code}: {message} | data={rendered}")
        else:
            super().__init__(f"ACP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


# ── High-level ACP client ───────────────────────────────────────────────────
class AcpClient:
    """ACP client that drives ``opencode acp`` over ``docker exec -i``.

    The lifecycle is **long-lived**: ``ensure_session()`` spawns the
    bridge on first use and reuses it for subsequent calls. If the
    underlying process dies (container restart, OOM, …) the next call
    transparently respawns.
    """

    def __init__(
        self,
        *,
        container: str,
        docker_bin: str = "docker",
        workspace_root: str = "/workspace/project",
        permission_policy: str = "workspace",
        client_info: dict[str, str] | None = None,
        # Override the command if you want to test against a local opencode
        # binary directly (`["opencode", "acp"]`).
        command: list[str] | None = None,
        initialize_extra: dict[str, Any] | None = None,
    ) -> None:
        self._container = container
        self._docker_bin = docker_bin
        self._workspace_root = workspace_root
        self._permission_policy = permission_policy
        self._client_info = client_info or {"name": "opencode-adapter", "version": "1.0.0"}
        self._command = command or [
            docker_bin,
            "exec",
            "-i",
            container,
            "opencode",
            "acp",
        ]
        self._initialize_extra = initialize_extra or {}

        self._proc: asyncio.subprocess.Process | None = None
        self._peer: JsonRpcStdioPeer | None = None
        self._lock = asyncio.Lock()
        self._initialized = False
        # session_id -> asyncio.Queue[AcpSessionUpdate] for active prompt turns.
        self._update_streams: dict[str, asyncio.Queue[AcpSessionUpdate]] = {}
        # Tracks last seen agent capabilities (returned by initialize).
        self.agent_capabilities: dict[str, Any] = {}

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def ensure_initialized(self, *, initialize_timeout_s: float = 30.0) -> None:
        async with self._lock:
            if self._initialized and self._peer is not None and not self._peer._closed.is_set():
                return
            await self._spawn()
            assert self._peer is not None
            params: dict[str, Any] = {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": CLIENT_CAPABILITIES,
                "clientInfo": dict(self._client_info),
            }
            params.update(self._initialize_extra)
            try:
                result = await self._peer.call(
                    "initialize", params, timeout_s=initialize_timeout_s
                )
            except Exception as exc:
                await self.aclose()
                raise ConnectionError(f"ACP initialize failed: {exc}") from exc
            if isinstance(result, dict):
                self.agent_capabilities = result.get("agentCapabilities") or {}
            self._initialized = True

    async def _spawn(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._peer = JsonRpcStdioPeer(
            self._proc,
            request_handler=self._handle_request,
            notification_handler=self._handle_notification,
            on_close=self._on_peer_closed,
        )

    def _on_peer_closed(self) -> None:
        self._initialized = False
        # Wake any prompt turns waiting on per-session queues.
        for q in self._update_streams.values():
            try:
                q.put_nowait(
                    AcpSessionUpdate(session_id="", kind="__closed__", raw={"closed": True})
                )
            except Exception:
                pass

    async def aclose(self) -> None:
        async with self._lock:
            if self._peer is not None:
                try:
                    await self._peer.aclose()
                except Exception:
                    pass
                self._peer = None
            if self._proc is not None:
                try:
                    if self._proc.returncode is None:
                        self._proc.terminate()
                        try:
                            await asyncio.wait_for(self._proc.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            self._proc.kill()
                            await self._proc.wait()
                except ProcessLookupError:
                    pass
                self._proc = None
            self._initialized = False
            self._update_streams.clear()

    # ── high-level helpers (Agent methods) ──────────────────────────────────
    async def session_new(
        self,
        *,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        timeout_s: float = 60.0,
    ) -> str:
        await self.ensure_initialized()
        assert self._peer is not None
        params: dict[str, Any] = {
            "cwd": cwd,
            "mcpServers": _normalize_mcp_servers(mcp_servers),
        }
        result = await self._peer.call("session/new", params, timeout_s=timeout_s)
        if not isinstance(result, dict) or "sessionId" not in result:
            raise AcpError(-32000, f"session/new returned unexpected payload: {result!r}")
        return str(result["sessionId"])

    async def session_set_mode(self, session_id: str, mode_id: str, *, timeout_s: float = 15.0) -> None:
        await self.ensure_initialized()
        assert self._peer is not None
        await self._peer.call(
            "session/set_mode",
            {"sessionId": session_id, "modeId": mode_id},
            timeout_s=timeout_s,
        )

    async def session_cancel(self, session_id: str) -> None:
        if self._peer is None:
            return
        try:
            await self._peer.notify("session/cancel", {"sessionId": session_id})
        except Exception as exc:
            logger.warning("session/cancel(%s) failed: %s", session_id, exc)

    async def session_prompt(
        self,
        session_id: str,
        *,
        prompt_blocks: list[dict[str, Any]],
        timeout_s: float = 1200.0,
    ) -> AcpPromptResult:
        await self.ensure_initialized()
        assert self._peer is not None
        queue: asyncio.Queue[AcpSessionUpdate] = asyncio.Queue()
        self._update_streams[session_id] = queue
        started = time.time()
        text_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        files_changed: list[str] = []
        updates: list[AcpSessionUpdate] = []
        try:
            call_task = asyncio.create_task(
                self._peer.call(
                    "session/prompt",
                    {"sessionId": session_id, "prompt": prompt_blocks},
                    timeout_s=timeout_s,
                )
            )

            async def _drain_updates() -> None:
                while True:
                    upd = await queue.get()
                    if upd.kind == "__closed__":
                        return
                    updates.append(upd)
                    if upd.kind in ("agent_message_chunk", "user_message_chunk"):
                        if upd.text:
                            text_parts.append(upd.text)
                    elif upd.kind == "agent_thought_chunk":
                        if upd.text:
                            thought_parts.append(upd.text)
                    elif upd.kind == "tool_call":
                        if upd.raw:
                            tool_calls.append(dict(upd.raw))
                        for p in upd.paths:
                            if p not in files_changed and _is_edit_kind(upd.tool_kind):
                                files_changed.append(p)
                    elif upd.kind == "tool_call_update":
                        for p in upd.paths:
                            if p not in files_changed and _is_edit_kind(upd.tool_kind):
                                files_changed.append(p)

            drain_task = asyncio.create_task(_drain_updates())
            try:
                response = await call_task
            finally:
                drain_task.cancel()
                try:
                    await drain_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            self._update_streams.pop(session_id, None)
        stop_reason = ""
        if isinstance(response, dict):
            stop_reason = str(response.get("stopReason") or "")
        return AcpPromptResult(
            stop_reason=stop_reason,
            text="".join(text_parts).strip(),
            thoughts="".join(thought_parts).strip(),
            files_changed=files_changed,
            tool_calls=tool_calls,
            updates=updates,
            started_at=started,
            duration_s=round(time.time() - started, 3),
        )

    # ── incoming dispatch ───────────────────────────────────────────────────
    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "session/update":
            session_id = str(params.get("sessionId") or "")
            update = params.get("update") or {}
            if not isinstance(update, dict):
                return
            kind = str(update.get("sessionUpdate") or "")
            text = ""
            content = update.get("content")
            if isinstance(content, dict):
                v = content.get("text")
                if isinstance(v, str):
                    text = v
                else:
                    parts = content.get("content")
                    if isinstance(parts, list):
                        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
            tool_call_id = str(update.get("toolCallId") or "")
            tool_name = ""
            tool_kind = ""
            tool_status = str(update.get("status") or "")
            paths: list[str] = []
            if kind in ("tool_call", "tool_call_update"):
                tool_name = str(update.get("title") or update.get("toolName") or "")
                tool_kind = str(update.get("kind") or "")
                paths = _collect_paths_from_tool_call(update)
            upd_record = AcpSessionUpdate(
                session_id=session_id,
                kind=kind,
                text=text,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_kind=tool_kind,
                tool_status=tool_status,
                paths=paths,
                raw=dict(update),
            )
            queue = self._update_streams.get(session_id)
            if queue is not None:
                await queue.put(upd_record)
            else:
                logger.debug(
                    "session/update for unknown session %s (kind=%s)", session_id, kind
                )
        # Other notifications (current_mode_update, plan, …) are accepted
        # silently — they piggyback on session/update in the schema and
        # already flow through the same handler above.

    async def _handle_request(self, method: str, params: dict[str, Any]) -> Any:
        if method in ("fs/read_text_file", "fs/readTextFile"):
            return await self._handle_fs_read(params)
        if method in ("fs/write_text_file", "fs/writeTextFile"):
            return await self._handle_fs_write(params)
        if method in ("session/request_permission", "session/requestPermission"):
            return await self._handle_request_permission(params)
        if method.startswith("terminal/"):
            return await self._handle_terminal(method, params)
        raise AcpError(-32601, f"method not implemented by adapter: {method}")

    # ── fs handlers ─────────────────────────────────────────────────────────
    async def _handle_fs_read(self, params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or "")
        if not path:
            raise AcpError(-32602, "fs/read_text_file: missing path")
        if not Path(path).is_absolute():
            raise AcpError(-32602, "fs/read_text_file: path must be absolute")
        if not _is_under(path, self._workspace_root):
            raise AcpError(-32001, f"fs/read_text_file: path outside workspace ({self._workspace_root})")
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise AcpError(-32002, f"fs/read_text_file: {exc}") from exc
        except OSError as exc:
            raise AcpError(-32003, f"fs/read_text_file: {exc}") from exc
        line = params.get("line")
        limit = params.get("limit")
        if isinstance(line, int) and line > 0:
            lines = text.splitlines(keepends=True)
            start = max(0, line - 1)
            end = len(lines) if not isinstance(limit, int) or limit <= 0 else min(len(lines), start + limit)
            text = "".join(lines[start:end])
        elif isinstance(limit, int) and limit > 0:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:limit])
        return {"content": text}

    async def _handle_fs_write(self, params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or "")
        content = params.get("content")
        if not path:
            raise AcpError(-32602, "fs/write_text_file: missing path")
        if not isinstance(content, str):
            raise AcpError(-32602, "fs/write_text_file: content must be string")
        if not Path(path).is_absolute():
            raise AcpError(-32602, "fs/write_text_file: path must be absolute")
        if not _is_under(path, self._workspace_root):
            raise AcpError(-32001, f"fs/write_text_file: path outside workspace ({self._workspace_root})")
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content, encoding="utf-8")
        except OSError as exc:
            raise AcpError(-32003, f"fs/write_text_file: {exc}") from exc
        return {}

    # ── permission handler ──────────────────────────────────────────────────
    async def _handle_request_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_call = params.get("toolCall") or {}
        options = params.get("options") or []
        if not isinstance(tool_call, dict):
            tool_call = {}
        if not isinstance(options, list):
            options = []
        outcome = decide_permission(
            tool_call, options, self._permission_policy, self._workspace_root
        )
        decision = "approve" if outcome.get("outcome") == "selected" else "deny"
        logger.info(
            "ACP permission: tool=%s status=%s policy=%s → %s",
            tool_call.get("title") or tool_call.get("toolName") or "?",
            tool_call.get("status") or "pending",
            self._permission_policy,
            decision,
        )
        return {"outcome": outcome}

    # ── terminal handlers ───────────────────────────────────────────────────
    async def _handle_terminal(self, method: str, params: dict[str, Any]) -> Any:
        # Lazy-import to keep the module import-time cost low.
        from terminals import TerminalManager  # type: ignore

        if not hasattr(self, "_terminals"):
            self._terminals = TerminalManager(
                docker_bin=self._docker_bin,
                container=self._container,
                workspace_root=self._workspace_root,
            )
        manager: TerminalManager = self._terminals  # type: ignore[attr-defined]
        if method == "terminal/create":
            return await manager.create(params)
        if method == "terminal/output":
            return await manager.output(params)
        if method == "terminal/wait_for_exit":
            return await manager.wait_for_exit(params)
        if method == "terminal/release":
            return await manager.release(params)
        if method == "terminal/kill":
            return await manager.kill(params)
        raise AcpError(-32601, f"terminal method not implemented: {method}")


# ── helpers ────────────────────────────────────────────────────────────────
def _is_edit_kind(kind: str) -> bool:
    """Heuristic: ACP ToolKind values that imply a filesystem mutation.

    Spec enumerates: ``read``, ``edit``, ``delete``, ``move``, ``execute``,
    ``search``, ``think``, ``fetch``, ``other``. We treat the first four
    plus ``move`` as `files_changed` contributors.
    """
    k = (kind or "").lower()
    return k in {"edit", "delete", "move", "create"}


def _normalize_mcp_servers(servers: list[Any] | None) -> list[dict[str, Any]]:
    """Coerce ``Skill.mcp_servers`` / env entries to ACP McpServer dicts.

    opencode's ACP schema is strict: every server entry must declare
    ``headers: []`` for ``http`` transport and ``args: []`` for ``stdio``.
    ``sse`` is accepted by opencode at the transport level but the schema
    validator only knows ``http`` and ``stdio`` — SSE endpoints work fine
    when announced as ``http`` (opencode auto-detects SSE framing from
    the response stream).
    """
    if not servers:
        return []

    def _as_kv_list(value: Any) -> list[dict[str, str]]:
        if isinstance(value, list):
            out: list[dict[str, str]] = []
            for item in value:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("value"), str)
                ):
                    out.append({"name": item["name"], "value": item["value"]})
            return out
        if isinstance(value, dict):
            return [
                {"name": str(k), "value": str(v)}
                for k, v in value.items()
                if isinstance(k, (str, int))
            ]
        return []

    out: list[dict[str, Any]] = []
    for entry in servers:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("id") or "").strip()
        if not name:
            continue
        transport = str(entry.get("type") or entry.get("transport") or "").lower()
        url = entry.get("url")
        if transport in ("http", "streamable_http") and isinstance(url, str):
            out.append({
                "name": name,
                "type": "http",
                "url": url,
                "headers": _as_kv_list(entry.get("headers")),
            })
            continue
        if transport in ("sse",) and isinstance(url, str):
            out.append({
                "name": name,
                "type": "sse",
                "url": url,
                "headers": _as_kv_list(entry.get("headers")),
            })
            continue
        if "command" in entry:
            out.append({
                "name": name,
                "type": "stdio",
                "command": entry["command"],
                "args": list(entry["args"]) if isinstance(entry.get("args"), list) else [],
                "env": _as_kv_list(entry.get("env")),
            })
            continue
        # Last resort — keep original keys but enforce minimum structure
        # opencode's strict schema demands.
        srv = {"name": name}
        srv.update({k: v for k, v in entry.items() if k != "name"})
        srv.setdefault("type", "sse")
        srv.setdefault("headers", [])
        out.append(srv)
    return out


def make_text_prompt_blocks(text: str) -> list[dict[str, Any]]:
    """Wrap a plain string into the ACP ContentBlock[] shape."""
    if not text:
        return []
    return [{"type": "text", "text": text}]
