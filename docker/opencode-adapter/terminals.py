"""ACP terminal/* implementation for the opencode-adapter.

opencode (as Agent) calls ``terminal/create`` on the Client (us) when it
needs to run a shell command inside the project. We forward the command
to ``docker exec`` so the working directory and tooling match exactly
what the user sees in ``docker exec -it opencode bash``.

Each terminal has its own UUID, captured stdout/stderr buffer, and a
``wait_for_exit`` future. Output is byte-bounded
(``outputByteLimit`` field of CreateTerminalRequest); when the cap is
exceeded we keep the *tail* and mark the head as truncated.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("opencode-adapter.terminals")


@dataclass
class _Terminal:
    terminal_id: str
    process: asyncio.subprocess.Process
    cwd: str
    command: str
    args: list[str]
    output_byte_limit: int = 1_048_576  # 1 MiB
    started_at: float = field(default_factory=time.time)
    stdout_buf: bytearray = field(default_factory=bytearray)
    stderr_buf: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    exited: bool = False
    exit_code: int | None = None
    exit_signal: str | None = None
    exit_event: asyncio.Event = field(default_factory=asyncio.Event)
    _reader_tasks: list[asyncio.Task[None]] = field(default_factory=list)


class TerminalManager:
    """Container-aware terminal manager driven by ACP RPCs.

    Commands run inside the opencode container via ``docker exec`` so the
    sandbox stays consistent (same user, same filesystem view).
    """

    def __init__(self, *, docker_bin: str, container: str, workspace_root: str) -> None:
        self._docker_bin = docker_bin
        self._container = container
        self._workspace_root = workspace_root
        self._terminals: dict[str, _Terminal] = {}
        self._lock = asyncio.Lock()

    # ── create ──────────────────────────────────────────────────────────────
    async def create(self, params: dict[str, Any]) -> dict[str, Any]:
        command = params.get("command")
        if not isinstance(command, str) or not command:
            raise _err(-32602, "terminal/create: command must be non-empty string")
        args_param = params.get("args") or []
        if not isinstance(args_param, list):
            raise _err(-32602, "terminal/create: args must be array")
        args: list[str] = [str(a) for a in args_param]
        cwd = str(params.get("cwd") or self._workspace_root) or self._workspace_root
        env_pairs = params.get("env") or []
        output_byte_limit = int(params.get("outputByteLimit") or 1_048_576)

        # Build the docker exec invocation. We always run inside the
        # container, even for read-only commands, to avoid leaking adapter
        # binaries / paths into outputs.
        exec_cmd: list[str] = [self._docker_bin, "exec", "-i", "-w", cwd]
        if isinstance(env_pairs, list):
            for entry in env_pairs:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                value = entry.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    exec_cmd.extend(["-e", f"{name}={value}"])
        exec_cmd.append(self._container)
        exec_cmd.append(command)
        exec_cmd.extend(args)

        logger.info(
            "terminal/create id=… cwd=%s cmd=%s args=%s",
            cwd,
            command,
            " ".join(shlex.quote(a) for a in args[:6]),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=None,
            )
        except FileNotFoundError as exc:
            raise _err(-32003, f"terminal/create: docker binary missing: {exc}") from exc

        terminal_id = uuid.uuid4().hex
        term = _Terminal(
            terminal_id=terminal_id,
            process=proc,
            cwd=cwd,
            command=command,
            args=args,
            output_byte_limit=output_byte_limit,
        )
        async with self._lock:
            self._terminals[terminal_id] = term
        term._reader_tasks.append(asyncio.create_task(self._consume(term, "stdout")))
        term._reader_tasks.append(asyncio.create_task(self._consume(term, "stderr")))
        term._reader_tasks.append(asyncio.create_task(self._wait(term)))
        return {"terminalId": terminal_id}

    async def _consume(self, term: _Terminal, stream_name: str) -> None:
        stream = getattr(term.process, stream_name)
        if stream is None:
            return
        buf = term.stdout_buf if stream_name == "stdout" else term.stderr_buf
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > term.output_byte_limit:
                    # Truncate from the head to keep within budget while
                    # preserving the most recent output (typical for logs).
                    excess = len(buf) - term.output_byte_limit
                    del buf[:excess]
                    term.truncated = True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("terminal %s %s reader failed", term.terminal_id, stream_name)

    async def _wait(self, term: _Terminal) -> None:
        try:
            rc = await term.process.wait()
            term.exited = True
            term.exit_code = rc
            term.exit_event.set()
        except asyncio.CancelledError:
            raise

    # ── output / wait_for_exit / kill / release ─────────────────────────────
    def _get(self, params: dict[str, Any]) -> _Terminal:
        tid = str(params.get("terminalId") or "")
        if not tid:
            raise _err(-32602, "terminal/*: missing terminalId")
        term = self._terminals.get(tid)
        if term is None:
            raise _err(-32004, f"terminal/*: unknown terminalId {tid}")
        return term

    async def output(self, params: dict[str, Any]) -> dict[str, Any]:
        term = self._get(params)
        out = term.stdout_buf.decode("utf-8", errors="replace")
        err = term.stderr_buf.decode("utf-8", errors="replace")
        combined = out + (("\n" + err) if err else "")
        resp: dict[str, Any] = {
            "output": combined,
            "truncated": term.truncated,
        }
        if term.exited:
            resp["exitStatus"] = {
                "exitCode": term.exit_code if term.exit_code is not None else None,
                "signal": term.exit_signal,
            }
        return resp

    async def wait_for_exit(self, params: dict[str, Any]) -> dict[str, Any]:
        term = self._get(params)
        await term.exit_event.wait()
        return {
            "exitStatus": {
                "exitCode": term.exit_code if term.exit_code is not None else None,
                "signal": term.exit_signal,
            }
        }

    async def kill(self, params: dict[str, Any]) -> dict[str, Any]:
        term = self._get(params)
        if not term.exited:
            try:
                term.process.kill()
            except ProcessLookupError:
                pass
            except Exception:
                logger.exception("terminal %s kill failed", term.terminal_id)
            try:
                await asyncio.wait_for(term.exit_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        return {}

    async def release(self, params: dict[str, Any]) -> dict[str, Any]:
        term = self._get(params)
        if not term.exited:
            try:
                term.process.kill()
            except ProcessLookupError:
                pass
        for task in term._reader_tasks:
            if not task.done():
                task.cancel()
        async with self._lock:
            self._terminals.pop(term.terminal_id, None)
        return {}


def _err(code: int, message: str) -> Exception:
    # Mirror AcpError without importing it (would create a cycle).
    from acp_client import AcpError  # local import to avoid cycle on package load

    return AcpError(code, message)
