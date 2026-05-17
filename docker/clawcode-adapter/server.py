#!/usr/bin/env python3
"""clawcode-adapter — headless front-end for the Rust `claw` CLI.

The adapter shells out to ``docker exec clawcode claw --output-format json
prompt "<task>"`` and parses the streamed JSON envelope. The ``clawcode``
container itself is started independently by ``clawcode-start.sh --no-attach``;
this adapter only adds the network surface that the rest of the agent mesh
(OpenWebUI as a tool server, OpenHands and external A2A callers as MCP /
HTTP clients) needs to talk to it.

Why ``docker exec`` and not an in-process Rust call:
* the Rust workspace (``rust/crates/runtime``) is already built into the
  ``voice-assistant-clawcode:local`` image and wired up with the right env
  (OPENAI_API_BASE, MCP_SERVERS, /workspace bind-mount);
* the adapter image stays tiny (no Rust toolchain needed) and the same exec
  channel works for the phase-2 persistent-session mode (a long-lived
  ``docker exec -i clawcode claw`` PTY pipe).

Confirmed upstream contract (ultraworkers/claw-code rust/README.md as of
April 2026): ``claw [--model M] [--output-format text|json] prompt "<task>"``
is a one-shot non-interactive run. There is no ``-p``/``--print``/``--task``
flag — the ``prompt`` subcommand is the canonical headless entry point.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from typing import Any

from fastapi import HTTPException

from agent_mesh_adapter import (
    AdapterConfig,
    RunResult,
    Runner,
    SessionState,
    SessionStore,
    SkillsBundle,
    build_app,
    run_subprocess,
    session_state_for_api,
)

logger = logging.getLogger("clawcode-adapter")

CLAWCODE_CONTAINER = os.environ.get("CLAWCODE_CONTAINER", "clawcode")
CLAWCODE_MODEL = os.environ.get(
    "CLAWCODE_DEFAULT_MODEL", "openai/qwen3.6-35b-heretic"
)
CLAWCODE_WORKSPACE = os.environ.get(
    "CLAWCODE_WORKSPACE_IN_CONTAINER", "/workspace/project"
)
CONCURRENCY = int(os.environ.get("CLAWCODE_ADAPTER_CONCURRENCY", "2"))
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
# `claw` does not yet have a stable `--system` flag in older builds; allow
# operators to override / disable the flag through env so the adapter keeps
# working even if upstream renames it. Falsy ("", "0", "off") disables the
# flag entirely; the system prompt is then prepended to the task body
# instead.
CLAWCODE_SYSTEM_FLAG = os.environ.get("CLAWCODE_SYSTEM_FLAG", "--system").strip()


def _extract_result_text(stdout: str) -> str:
    """Best-effort extraction of the agent's final response from claw output.

    ``claw --output-format json prompt`` streams a sequence of JSON Lines
    (one event per line: ``tool_use``, ``message``, ``done`` …). We grab the
    last ``message``/``assistant`` text we see; if the format is plain text
    (older builds or non-JSON output), return as-is.
    """
    final_text: list[str] = []
    json_seen = False
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not (line.startswith("{") or line.startswith("[")):
            final_text.append(line)
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            final_text.append(line)
            continue
        json_seen = True
        text = _extract_text_from_event(event)
        if text:
            final_text.append(text)

    if not json_seen and not final_text:
        return stdout.strip()
    return "\n".join(final_text).strip()


def _extract_text_from_event(event: Any) -> str:
    if isinstance(event, str):
        return event
    if not isinstance(event, dict):
        return ""
    for key in ("text", "content", "message", "response"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    inner = _extract_text_from_event(item)
                    if inner:
                        parts.append(inner)
            joined = "\n".join(parts).strip()
            if joined:
                return joined
    if event.get("type") in {"message", "assistant", "final"}:
        if isinstance(event.get("data"), dict):
            return _extract_text_from_event(event["data"])
    return ""


def _system_flag_enabled(value: str) -> bool:
    return bool(value) and value.lower() not in {"0", "off", "false", "no"}


class ClawcodeRunner(Runner):
    agent_id = "clawcode"
    agent_label = "Claw Code (Rust headless CLI)"

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._sessions = SessionStore()
        self._system_prompt: str = ""
        self._mcp_servers: list[Any] = []

    def apply_skills(self, bundle: SkillsBundle) -> None:
        """Receive the latest :class:`SkillsBundle` from build_app.

        Called once at startup and again on ``POST /admin/reload``. The
        runner snapshots the system prompt and the merged MCP server
        list so subsequent ``docker exec claw`` invocations can see
        them through env vars / a flag, depending on what the installed
        ``claw`` build supports.
        """
        self._system_prompt = bundle.system_prompt or ""
        self._mcp_servers = list(bundle.mcp_servers or [])
        if bundle.skills:
            logger.info(
                "clawcode runner received %d skills (version=%s); "
                "system_prompt=%dB mcp_servers=%d",
                len(bundle.skills),
                bundle.version,
                len(self._system_prompt),
                len(self._mcp_servers),
            )

    def _workdir(self, workspace_subdir: str) -> str:
        if not workspace_subdir:
            return CLAWCODE_WORKSPACE
        sub = workspace_subdir.lstrip("/")
        if ".." in sub.split("/"):
            raise HTTPException(
                status_code=400,
                detail="workspace_subdir must not contain '..'",
            )
        return f"{CLAWCODE_WORKSPACE.rstrip('/')}/{sub}"

    def _serialize_mcp_servers(self) -> str:
        if not self._mcp_servers:
            return ""
        names: list[str] = []
        for entry in self._mcp_servers:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("id") or "").strip()
                if name:
                    names.append(name)
        if not names:
            return ""
        try:
            return json.dumps(self._mcp_servers, ensure_ascii=False)
        except (TypeError, ValueError):
            return ",".join(names)

    def _exec_command(
        self,
        *,
        workdir: str,
        task: str,
        depth: int,
        json_output: bool = True,
    ) -> list[str]:
        cmd = [
            DOCKER_BIN,
            "exec",
            "-i",
            "-w",
            workdir,
            "-e",
            f"AGENT_MESH_DEPTH={depth}",
        ]
        # Forward the merged MCP server list so a freshly-spawned `claw`
        # picks them up via env (the Rust runtime reads `MCP_SERVERS`
        # JSON; the headless image's startup wrapper parses the same
        # env). Non-empty only when at least one skill declared servers.
        mcp_env = self._serialize_mcp_servers()
        if mcp_env:
            cmd.extend(["-e", f"MCP_SERVERS={mcp_env}"])
        # Make the system prompt visible as an env var too — helps when
        # the binary doesn't accept the flag.
        if self._system_prompt:
            cmd.extend(["-e", "AGENT_SYSTEM_PROMPT"])
        cmd.append(CLAWCODE_CONTAINER)
        cmd.extend(["claw", "--model", CLAWCODE_MODEL])
        if json_output:
            cmd.extend(["--output-format", "json"])
        if self._system_prompt and _system_flag_enabled(CLAWCODE_SYSTEM_FLAG):
            cmd.extend([CLAWCODE_SYSTEM_FLAG, self._system_prompt])
            effective_task = task
        elif self._system_prompt:
            effective_task = (
                f"{self._system_prompt.rstrip()}\n\n"
                f"---\n\n# Task\n\n{task}"
            )
        else:
            effective_task = task
        cmd.extend(["prompt", effective_task])
        return cmd

    def _exec_env(self) -> dict[str, str] | None:
        """Env applied to the `docker exec` *client* process.

        ``-e AGENT_SYSTEM_PROMPT`` above tells docker to forward the
        same-named var from this dict into the container.
        """
        if not self._system_prompt:
            return None
        env = os.environ.copy()
        env["AGENT_SYSTEM_PROMPT"] = self._system_prompt
        return env

    async def _check_container(self) -> None:
        rc, stdout, stderr = await run_subprocess(
            [
                DOCKER_BIN,
                "inspect",
                "--format",
                "{{.State.Status}}",
                CLAWCODE_CONTAINER,
            ],
            timeout_s=10,
        )
        if rc != 0 or "running" not in stdout:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"clawcode container '{CLAWCODE_CONTAINER}' is not running. "
                    f"Start it first: `bash clawcode-start.sh --no-attach`. "
                    f"stderr_tail={stderr!r}"
                ),
            )

    async def run_one_shot(
        self,
        *,
        task: str,
        workspace_subdir: str,
        timeout_s: int,
        depth: int,
    ) -> RunResult:
        await self._check_container()
        workdir = self._workdir(workspace_subdir)
        started = time.time()
        async with self._sem:
            cmd = self._exec_command(
                workdir=workdir, task=task, depth=depth, json_output=True
            )
            logger.info(
                "clawcode run depth=%d workdir=%s task_len=%d cmd=%s",
                depth,
                workdir,
                len(task),
                " ".join(shlex.quote(c) for c in cmd[:6]),
            )
            rc, stdout, stderr = await run_subprocess(
                cmd, timeout_s=timeout_s, env=self._exec_env()
            )
        duration = round(time.time() - started, 3)
        if rc == -1 and stderr.startswith("timeout"):
            return RunResult(
                ok=False,
                exit_code=124,
                logs_tail=stderr,
                duration_s=duration,
                depth=depth,
                error=f"claw exec exceeded timeout {timeout_s}s",
            )
        result_text = _extract_result_text(stdout)
        return RunResult(
            ok=(rc == 0),
            result_text=result_text,
            files_changed=[],
            logs_tail=stderr[-4096:] if stderr else "",
            exit_code=rc,
            duration_s=duration,
            depth=depth,
            error=None if rc == 0 else f"claw exited with code {rc}",
        )

    async def create_session(
        self,
        *,
        workspace_subdir: str,
        title: str,
        depth: int,
    ) -> SessionState:
        await self._check_container()
        # Validate subdir at session-creation time so the LLM finds out early.
        self._workdir(workspace_subdir)
        record = await self._sessions.create(
            agent=self.agent_id,
            workspace_subdir=workspace_subdir,
            title=title,
        )
        record.extra["depth"] = depth
        record.extra["history"] = []
        return session_state_for_api(record.state, record.extra)

    async def post_message(
        self,
        *,
        session_id: str,
        message: str,
        timeout_s: int,
        depth: int,
    ) -> SessionState:
        record = await self._sessions.get(session_id)
        if record.state.agent != self.agent_id:
            raise HTTPException(status_code=404, detail="session not found")
        await self._check_container()
        async with record.lock:
            record.state.status = "running"
            workdir = self._workdir(record.state.workspace_subdir)
            history: list[dict[str, str]] = record.extra.setdefault("history", [])
            history.append({"role": "user", "content": message})
            # Phase 2 implementation note: until Claw's REPL accepts a stable
            # programmatic channel (per upstream's command-reference, the
            # ``--resume`` flag is the only persistent-session contract today),
            # we replay the conversation as a single prompt that recaps prior
            # turns. This keeps state on our side and works against any
            # version of claw without requiring REPL pty manipulation.
            recap = "\n\n".join(
                f"[{turn['role']}]\n{turn['content']}" for turn in history
            )
            cmd = self._exec_command(
                workdir=workdir, task=recap, depth=depth, json_output=True
            )
            rc, stdout, stderr = await run_subprocess(
                cmd, timeout_s=timeout_s, env=self._exec_env()
            )
            result_text = _extract_result_text(stdout)
            history.append({"role": "assistant", "content": result_text})
            record.state.messages_seen += 1
            record.state.last_active_at = time.time()
            record.state.status = "ready" if rc == 0 else "error"
            record.state.logs_tail = stderr[-4096:] if stderr else ""
            return session_state_for_api(record.state, record.extra)

    async def get_session(self, session_id: str) -> SessionState:
        record = await self._sessions.get(session_id)
        if record.state.agent != self.agent_id:
            raise HTTPException(status_code=404, detail="session not found")
        return session_state_for_api(record.state, record.extra)


def _build() -> Any:
    api_key = os.environ.get("CLAWCODE_ADAPTER_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "CLAWCODE_ADAPTER_API_KEY is empty — all /v1/* endpoints will be "
            "open. Set this in .env before exposing the adapter."
        )
    max_nested = int(os.environ.get("MAX_NESTED_AGENT_CALLS", "1"))
    skills_dir = (
        os.environ.get("AGENT_MESH_SKILLS_DIR", "").strip()
        or os.environ.get("SKILLS_DIR", "").strip()
        or None
    )
    config = AdapterConfig(
        api_key=api_key,
        max_nested_agent_calls=max_nested,
        service_name="clawcode-adapter",
        title="Claw Code Adapter",
        description=(
            "OpenAPI + MCP SSE adapter for the Claw Code Rust CLI. One "
            "`POST /v1/run` call shells out to `docker exec clawcode claw "
            "--output-format json prompt \"<task>\"` and returns the agent's "
            "final answer. Cycle guard via `X-Agent-Mesh-Depth` "
            f"(max_nested_agent_calls={max_nested})."
        ),
        skills_dir=skills_dir,
    )
    runner = ClawcodeRunner()
    return build_app(runner, config)


app = _build()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "8790")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
