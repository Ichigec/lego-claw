#!/usr/bin/env python3
"""opencode-adapter — ACP-bridged front-end for the opencode coding agent.

Each ``POST /v1/run`` (or ``POST /a2a/v1/message:send``) call is mapped to
one ACP **prompt turn** inside a freshly-created session:

1. ``initialize`` — once per adapter lifetime, run on demand the first
   time we need to talk to opencode. We advertise the Client capability
   set ``{fs.{readTextFile, writeTextFile}: true, terminal: true}``.
2. ``session/new`` — sets ``cwd`` to the requested workspace subdir and
   declares the MCP servers gathered from the skills bundle.
3. ``session/prompt`` — wraps the task string in a ``ContentBlock[]`` of
   one ``text`` block. The response's ``stopReason`` plus the agent's
   streamed ``session/update`` notifications (``agent_message_chunk``,
   ``tool_call``, ``tool_call_update`` …) are aggregated into a
   :class:`RunResult`.

Connection robustness: a single long-lived ``docker exec -i opencode
opencode acp`` subprocess serves the whole adapter. If the underlying
container restarts, the next call detects ``BrokenPipeError`` (via the
JSON-RPC peer's ``ConnectionError`` surface) and respawns transparently.

Why a custom ACP client and not the upstream SDK? The Python ACP SDK is
unstable (mid-2026) and assumes direct control of stdin/stdout; we run
over ``docker exec`` and want minimal extra deps in the adapter image.
~400 lines of pure stdlib glue (see ``acp_client.py``) buys us full
spec compliance with deterministic behaviour.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import HTTPException

from acp_client import (
    AcpClient,
    AcpError,
    AcpPromptResult,
    make_text_prompt_blocks,
)
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

logger = logging.getLogger("opencode-adapter")

OPENCODE_CONTAINER = os.environ.get("OPENCODE_CONTAINER", "opencode")
OPENCODE_WORKSPACE = os.environ.get(
    "OPENCODE_WORKSPACE_IN_CONTAINER", "/workspace/project"
)
CONCURRENCY = int(os.environ.get("OPENCODE_ADAPTER_CONCURRENCY", "2"))
RUN_TIMEOUT_DEFAULT = int(os.environ.get("OPENCODE_ADAPTER_TIMEOUT", "1200"))
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
PERMISSION_POLICY = os.environ.get(
    "OPENCODE_ADAPTER_AUTO_APPROVE", "workspace"
).strip().lower()
# Some operators prefer to pre-pend the skills system prompt as a leading
# text block (cheap and works on every ACP backend). opencode also reads
# `AGENTS.md` natively from the workspace root, so this is a redundant
# but useful fallback when matched-skills should be force-attached.
INJECT_SYSTEM_PROMPT = os.environ.get(
    "OPENCODE_ADAPTER_INJECT_SYSTEM_PROMPT", "1"
).strip() not in {"0", "off", "false", "no"}


def _parse_mcp_server_catalog(raw: str | None) -> dict[str, dict[str, Any]]:
    """Parse ``OPENCODE_MCP_SERVERS`` JSON into a ``{name: entry}`` map.

    Skills declare MCP usage by *name* (`searchbox`, `clawcode-adapter`,
    `openhands-adapter`, …) but opencode's session/new requires the
    transport-specific structure (`{type, url, headers}` for SSE/HTTP or
    `{command, args, env}` for stdio). The catalog env var supplies the
    missing transport details so :class:`OpencodeRunner` can hydrate
    skill mcp_server names into full structures opencode accepts.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as exc:
        logger.warning("OPENCODE_MCP_SERVERS is not valid JSON: %s", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("id") or "").strip()
                if name:
                    out[name] = dict(entry, name=name)
    elif isinstance(data, dict):
        for name, entry in data.items():
            if isinstance(entry, dict):
                out[str(name)] = dict(entry, name=str(name))
    return out


MCP_SERVER_CATALOG: dict[str, dict[str, Any]] = _parse_mcp_server_catalog(
    os.environ.get("OPENCODE_MCP_SERVERS")
)


def _hydrate_mcp_servers(servers: list[Any]) -> list[Any]:
    """Resolve bare-name entries from :data:`MCP_SERVER_CATALOG`."""
    out: list[Any] = []
    for entry in servers:
        if isinstance(entry, str):
            mapped = MCP_SERVER_CATALOG.get(entry)
            if mapped is None:
                logger.warning(
                    "skill referenced MCP server %r but OPENCODE_MCP_SERVERS has no entry — dropping",
                    entry,
                )
                continue
            out.append(mapped)
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("id") or "").strip()
            if entry.get("url") or entry.get("command"):
                out.append(entry)
            elif name and name in MCP_SERVER_CATALOG:
                # Merge: skill metadata wins for shared keys, catalog
                # supplies transport defaults.
                merged = {**MCP_SERVER_CATALOG[name], **{k: v for k, v in entry.items() if v}}
                out.append(merged)
            elif name:
                logger.warning(
                    "skill MCP entry %r has no transport details and is not in OPENCODE_MCP_SERVERS — dropping",
                    name,
                )
        # else: ignore unknown shapes
    return out


class OpencodeRunner(Runner):
    agent_id = "opencode"
    agent_label = "opencode (ACP via stdio)"

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._sessions = SessionStore()
        self._system_prompt: str = ""
        self._mcp_servers: list[Any] = []
        self._acp = AcpClient(
            container=OPENCODE_CONTAINER,
            docker_bin=DOCKER_BIN,
            workspace_root=OPENCODE_WORKSPACE,
            permission_policy=PERMISSION_POLICY,
        )

    def apply_skills(self, bundle: SkillsBundle) -> None:
        """Snapshot the latest system prompt + merged MCP servers.

        Note: opencode reads ``AGENTS.md`` from ``/workspace/project``
        natively, so we deliberately keep the system-prompt injection
        light — only matched skills, not the full AGENTS.md + router.md
        stack. See the plan's «Risks» section for the rationale.
        """
        self._mcp_servers = _hydrate_mcp_servers(list(bundle.mcp_servers or []))
        if not bundle.skills:
            self._system_prompt = ""
            return
        prompt_chunks: list[str] = []
        for skill in bundle.skills:
            body = getattr(skill, "body", "")
            if not body:
                continue
            prompt_chunks.append(
                f"# Skill: {skill.id} (v{skill.version})\n"
                f"_tags: {', '.join(skill.tags) or '-'}_  "
                f"_triggers: {', '.join(skill.triggers) or '-'}_\n\n"
                f"{body.strip()}"
            )
        self._system_prompt = "\n\n---\n\n".join(prompt_chunks)
        logger.info(
            "opencode runner received %d skills (version=%s); "
            "system_prompt=%dB mcp_servers=%d",
            len(bundle.skills),
            bundle.version,
            len(self._system_prompt),
            len(self._mcp_servers),
        )

    def _workdir(self, workspace_subdir: str) -> str:
        if not workspace_subdir:
            return OPENCODE_WORKSPACE
        sub = workspace_subdir.lstrip("/")
        if ".." in sub.split("/"):
            raise HTTPException(
                status_code=400,
                detail="workspace_subdir must not contain '..'",
            )
        return f"{OPENCODE_WORKSPACE.rstrip('/')}/{sub}"

    async def _check_container(self) -> None:
        rc, stdout, stderr = await run_subprocess(
            [
                DOCKER_BIN,
                "inspect",
                "--format",
                "{{.State.Status}}",
                OPENCODE_CONTAINER,
            ],
            timeout_s=10,
        )
        if rc != 0 or "running" not in stdout:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"opencode container '{OPENCODE_CONTAINER}' is not running. "
                    f"Start it first: `bash opencode-start.sh --no-attach`. "
                    f"stderr_tail={stderr!r}"
                ),
            )

    def _build_prompt_blocks(self, task: str, depth: int) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if INJECT_SYSTEM_PROMPT and self._system_prompt:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "# Adapter system prompt (matched skills)\n\n"
                        f"{self._system_prompt}\n\n"
                        f"# Cycle guard\nX-Agent-Mesh-Depth={depth}. "
                        "Do not call run_opencode on yourself."
                    ),
                }
            )
        blocks.extend(make_text_prompt_blocks(task))
        return blocks

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
        budget = max(60, min(timeout_s, RUN_TIMEOUT_DEFAULT))
        async with self._sem:
            try:
                session_id = await self._acp.session_new(
                    cwd=workdir,
                    mcp_servers=self._mcp_servers,
                    timeout_s=min(60.0, float(budget)),
                )
            except (AcpError, ConnectionError, asyncio.TimeoutError) as exc:
                # One transparent retry: process might have died between
                # the last successful call and now. ``ensure_initialized``
                # respawns automatically.
                logger.warning("session/new failed (%s); reconnecting", exc)
                await self._acp.aclose()
                try:
                    session_id = await self._acp.session_new(
                        cwd=workdir,
                        mcp_servers=self._mcp_servers,
                        timeout_s=min(60.0, float(budget)),
                    )
                except Exception as exc2:
                    duration = round(time.time() - started, 3)
                    return RunResult(
                        ok=False,
                        result_text="",
                        files_changed=[],
                        logs_tail=str(exc2),
                        exit_code=1,
                        duration_s=duration,
                        depth=depth,
                        error=f"opencode session/new failed: {exc2}",
                    )

            prompt_blocks = self._build_prompt_blocks(task, depth)
            logger.info(
                "opencode run depth=%d workdir=%s task_len=%d session=%s budget=%ds",
                depth,
                workdir,
                len(task),
                session_id,
                budget,
            )
            try:
                outcome: AcpPromptResult = await self._acp.session_prompt(
                    session_id,
                    prompt_blocks=prompt_blocks,
                    timeout_s=float(budget),
                )
            except asyncio.TimeoutError:
                # Best-effort cancel so opencode releases LLM tokens early.
                await self._acp.session_cancel(session_id)
                duration = round(time.time() - started, 3)
                return RunResult(
                    ok=False,
                    exit_code=124,
                    logs_tail=f"timeout after {budget}s",
                    duration_s=duration,
                    depth=depth,
                    error=f"opencode exceeded timeout {budget}s",
                )
            except (AcpError, ConnectionError) as exc:
                duration = round(time.time() - started, 3)
                return RunResult(
                    ok=False,
                    exit_code=1,
                    logs_tail=str(exc),
                    duration_s=duration,
                    depth=depth,
                    error=f"opencode session/prompt failed: {exc}",
                )

        duration = round(time.time() - started, 3)
        ok_flag = outcome.stop_reason in ("end_turn", "")
        return RunResult(
            ok=ok_flag,
            result_text=outcome.text or outcome.thoughts,
            files_changed=outcome.files_changed,
            logs_tail=_logs_tail(outcome),
            exit_code=0 if ok_flag else 1,
            duration_s=duration,
            depth=depth,
            error=None if ok_flag else f"opencode stopped with {outcome.stop_reason!r}",
        )

    async def create_session(
        self,
        *,
        workspace_subdir: str,
        title: str,
        depth: int,
    ) -> SessionState:
        await self._check_container()
        workdir = self._workdir(workspace_subdir)
        try:
            session_id = await self._acp.session_new(
                cwd=workdir,
                mcp_servers=self._mcp_servers,
                timeout_s=60.0,
            )
        except (AcpError, ConnectionError, asyncio.TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=f"opencode session/new failed: {exc}") from exc
        record = await self._sessions.create(
            agent=self.agent_id,
            workspace_subdir=workspace_subdir,
            title=title,
        )
        record.extra["depth"] = depth
        record.extra["history"] = []
        record.extra["acp_session_id"] = session_id
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
            history: list[dict[str, str]] = record.extra.setdefault("history", [])
            history.append({"role": "user", "content": message})
            acp_session = record.extra.get("acp_session_id")
            if not acp_session:
                workdir = self._workdir(record.state.workspace_subdir)
                try:
                    acp_session = await self._acp.session_new(
                        cwd=workdir,
                        mcp_servers=self._mcp_servers,
                        timeout_s=60.0,
                    )
                    record.extra["acp_session_id"] = acp_session
                except (AcpError, ConnectionError, asyncio.TimeoutError) as exc:
                    record.state.status = "error"
                    record.state.logs_tail = str(exc)
                    return session_state_for_api(record.state, record.extra)
            blocks = self._build_prompt_blocks(message, depth)
            budget = max(60, min(timeout_s, RUN_TIMEOUT_DEFAULT))
            try:
                outcome = await self._acp.session_prompt(
                    acp_session, prompt_blocks=blocks, timeout_s=float(budget)
                )
            except (AcpError, ConnectionError, asyncio.TimeoutError) as exc:
                record.state.status = "error"
                record.state.logs_tail = str(exc)
                history.append({"role": "assistant", "content": f"[error] {exc}"})
                return session_state_for_api(record.state, record.extra)
            history.append({"role": "assistant", "content": outcome.text or outcome.thoughts})
            record.state.messages_seen += 1
            record.state.last_active_at = time.time()
            record.state.status = "ready" if outcome.stop_reason in ("end_turn", "") else "error"
            record.state.logs_tail = _logs_tail(outcome)
            return session_state_for_api(record.state, record.extra)

    async def get_session(self, session_id: str) -> SessionState:
        record = await self._sessions.get(session_id)
        if record.state.agent != self.agent_id:
            raise HTTPException(status_code=404, detail="session not found")
        return session_state_for_api(record.state, record.extra)


def _logs_tail(outcome: AcpPromptResult) -> str:
    """Compact diagnostic tail for the RunResult."""
    lines: list[str] = [f"stopReason={outcome.stop_reason or '?'}"]
    for upd in outcome.updates[-32:]:
        if upd.kind in ("tool_call", "tool_call_update"):
            lines.append(
                f"{upd.kind} id={upd.tool_call_id} title={upd.tool_name!r} "
                f"kind={upd.tool_kind} status={upd.tool_status}"
            )
        elif upd.kind in ("agent_message_chunk", "agent_thought_chunk", "user_message_chunk"):
            text = (upd.text or "").strip().replace("\n", " ")
            if text:
                lines.append(f"{upd.kind}: {text[:160]}")
        elif upd.kind:
            lines.append(f"{upd.kind}")
    return "\n".join(lines)[-4096:]


def _build() -> Any:
    api_key = os.environ.get("OPENCODE_ADAPTER_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "OPENCODE_ADAPTER_API_KEY is empty — all /v1/* endpoints will be "
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
        service_name="opencode-adapter",
        title="opencode Adapter",
        description=(
            "OpenAPI + MCP SSE adapter for opencode (https://opencode.ai/) "
            "bridged via ACP (Agent Client Protocol) JSON-RPC over "
            "`docker exec -i opencode opencode acp`. One `POST /v1/run` "
            "spawns one ACP session (`session/new` → `session/prompt`) "
            "and returns the agent's final answer + files_changed. "
            "Cycle guard via `X-Agent-Mesh-Depth` "
            f"(max_nested_agent_calls={max_nested})."
        ),
        skills_dir=skills_dir,
    )
    runner = OpencodeRunner()
    return build_app(runner, config)


app = _build()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "8798")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
