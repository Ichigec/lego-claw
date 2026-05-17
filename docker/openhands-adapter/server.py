#!/usr/bin/env python3
"""openhands-adapter — headless front-end for the OpenHands 1.6 CLI.

Each ``POST /v1/run`` spawns a fresh ``openhands --headless --json -t "<task>"``
subprocess. The CLI itself talks to ``docker.sock`` to bring up a transient
runtime-sandbox container (image ``ghcr.io/openhands/agent-server``), runs the
task to completion, and tears the sandbox down — i.e. one HTTP call = one
ephemeral sandbox. The adapter only adds:

* A small ``Semaphore`` so we cap concurrent runs (sandbox image is multi-GB
  and warm-up is ~30 s; running more than a couple in parallel kills a dev
  box).
* A best-effort sandbox cleanup: if the CLI leaks a runtime container we
  remove it after the task exits.
* OpenAPI + MCP SSE surfaces matching ``clawcode-adapter``.

Headless invocation contract is documented in docs.openhands.dev/openhands/
usage/cli/headless (verified for 1.6 — flag set ``--headless``, ``-t``,
``--json``, ``--override-with-envs``). For machine consumption we always pass
``--json``: each output line is one event, and the last ``message``-shaped
event is what we surface as ``result_text``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
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

logger = logging.getLogger("openhands-adapter")

CONCURRENCY = int(os.environ.get("OPENHANDS_ADAPTER_CONCURRENCY", "1"))
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")


def _resolve_openhands_cli() -> str | None:
    """Path to the OpenHands CLI (pip installs it next to ``sys.executable``)."""
    name = os.environ.get("OPENHANDS_BIN", "openhands").strip() or "openhands"
    candidate = Path(name)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if "/" not in name:
        sibling = Path(sys.executable).resolve().parent / name
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return None
DEFAULT_MODEL = os.environ.get("OPENHANDS_DEFAULT_MODEL", "qwen3.6-35b-heretic")
LITELLM_BASE_URL = os.environ.get(
    "OPENHANDS_LITELLM_BASE_URL", "http://host.docker.internal:4000/v1"
)
LITELLM_API_KEY = os.environ.get("OPENHANDS_LITELLM_API_KEY") or os.environ.get(
    "LITELLM_API_KEY", "sk-local"
)
WORKSPACE_ROOT = Path(
    os.environ.get("OPENHANDS_ADAPTER_WORKSPACE", "/workspace/project")
)
RUN_TIMEOUT_DEFAULT = int(os.environ.get("OPENHANDS_ADAPTER_TIMEOUT", "1800"))


def _llm_model() -> str:
    """Mirror compose.openhands.yml: prefix with `openai/` for LiteLLM."""
    raw = DEFAULT_MODEL.strip()
    if "/" in raw:
        return raw
    return f"openai/{raw}"


class OpenHandsRunner(Runner):
    agent_id = "openhands"
    agent_label = "OpenHands (headless agent server)"

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._sessions = SessionStore()
        self._max_default_timeout = RUN_TIMEOUT_DEFAULT
        self._system_prompt: str = ""
        self._mcp_servers: list[Any] = []
        cli = _resolve_openhands_cli()
        if cli:
            logger.info("OpenHands CLI resolved to %s", cli)
        else:
            logger.warning(
                "OpenHands CLI not found — rebuild the adapter image "
                "(requirements.txt includes the `openhands` package)"
            )

    def apply_skills(self, bundle: SkillsBundle) -> None:
        """Snapshot the latest system prompt + merged MCP servers.

        Called by ``build_app`` at startup and on ``POST /admin/reload``.
        Each ``run_one_shot`` then injects the values into the headless
        ``openhands`` subprocess via env vars.
        """
        self._system_prompt = bundle.system_prompt or ""
        self._mcp_servers = list(bundle.mcp_servers or [])
        if bundle.skills:
            logger.info(
                "openhands runner received %d skills (version=%s); "
                "system_prompt=%dB mcp_servers=%d",
                len(bundle.skills),
                bundle.version,
                len(self._system_prompt),
                len(self._mcp_servers),
            )

    def _workdir(self, workspace_subdir: str) -> Path:
        if not workspace_subdir:
            return WORKSPACE_ROOT
        sub = workspace_subdir.lstrip("/")
        if ".." in sub.split("/"):
            raise HTTPException(
                status_code=400,
                detail="workspace_subdir must not contain '..'",
            )
        return WORKSPACE_ROOT / sub

    def _serialize_mcp_servers(self) -> str:
        if not self._mcp_servers:
            return ""
        try:
            return json.dumps(self._mcp_servers, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""

    def _env(self, depth: int) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "LLM_API_KEY": LITELLM_API_KEY,
                "LLM_BASE_URL": LITELLM_BASE_URL,
                "LLM_MODEL": _llm_model(),
                "AGENT_MESH_DEPTH": str(depth),
            }
        )
        if self._system_prompt:
            # OpenHands honours `OH_SYSTEM_PROMPT` to prepend a system
            # message in headless mode (per docs.openhands.dev → cli →
            # advanced). Setting both names keeps the adapter compatible
            # across patch releases that renamed the env between 1.5/1.6.
            env["OH_SYSTEM_PROMPT"] = self._system_prompt
            env["AGENT_SYSTEM_PROMPT"] = self._system_prompt
        mcp_env = self._serialize_mcp_servers()
        if mcp_env:
            env["MCP_SERVERS"] = mcp_env
        return env

    async def _list_sandboxes(self) -> list[str]:
        rc, stdout, _ = await run_subprocess(
            [
                DOCKER_BIN,
                "ps",
                "-a",
                "--filter",
                "ancestor="
                + os.environ.get(
                    "OPENHANDS_AGENT_SERVER_REPO", "ghcr.io/openhands/agent-server"
                ),
                "--format",
                "{{.Names}}",
            ],
            timeout_s=10,
        )
        if rc != 0:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    async def _cleanup_sandboxes(self, before: set[str]) -> None:
        try:
            after = set(await self._list_sandboxes())
            leftovers = after - before
            for name in leftovers:
                logger.info("removing leftover openhands sandbox %s", name)
                await run_subprocess(
                    [DOCKER_BIN, "rm", "-f", name], timeout_s=15
                )
        except Exception as exc:
            logger.warning("sandbox cleanup failed: %s", exc)

    async def run_one_shot(
        self,
        *,
        task: str,
        workspace_subdir: str,
        timeout_s: int,
        depth: int,
    ) -> RunResult:
        oh_bin = _resolve_openhands_cli()
        if not oh_bin:
            name = os.environ.get("OPENHANDS_BIN", "openhands").strip() or "openhands"
            raise HTTPException(
                status_code=503,
                detail=(
                    f"`{name}` not found (PATH and next to {sys.executable!r}). "
                    "Install the PyPI package `openhands` in the image "
                    "(see docker/openhands-adapter/requirements.txt) and "
                    "rebuild: `docker compose -f compose.agents-mesh.yml build "
                    "--no-cache openhands-adapter && docker compose ... up -d "
                    "openhands-adapter`."
                ),
            )
        workdir = self._workdir(workspace_subdir)
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to ensure workspace dir {workdir}: {exc}",
            )
        cmd = [
            oh_bin,
            "--headless",
            "--json",
            "--override-with-envs",
            "--exit-without-confirmation",
            "--always-approve",
            "-t",
            task,
        ]
        budget = min(max(timeout_s, 60), self._max_default_timeout)
        started = time.time()
        async with self._sem:
            sandboxes_before = set(await self._list_sandboxes())
            logger.info(
                "openhands run depth=%d workdir=%s task_len=%d budget=%ds",
                depth,
                workdir,
                len(task),
                budget,
            )
            rc, stdout, stderr = await run_subprocess(
                cmd,
                cwd=str(workdir),
                env=self._env(depth),
                timeout_s=budget,
            )
            await self._cleanup_sandboxes(sandboxes_before)
        duration = round(time.time() - started, 3)
        if rc == -1 and stderr.startswith("timeout"):
            return RunResult(
                ok=False,
                exit_code=124,
                logs_tail=stderr,
                duration_s=duration,
                depth=depth,
                error=f"openhands exceeded timeout {budget}s",
            )
        result_text = _extract_last_message(stdout)
        files_changed = _collect_files_changed(stdout)
        return RunResult(
            ok=(rc == 0),
            result_text=result_text,
            files_changed=files_changed,
            logs_tail=(stderr or stdout)[-4096:],
            exit_code=rc,
            duration_s=duration,
            depth=depth,
            error=None if rc == 0 else f"openhands exited with code {rc}",
        )

    async def create_session(
        self,
        *,
        workspace_subdir: str,
        title: str,
        depth: int,
    ) -> SessionState:
        self._workdir(workspace_subdir)
        record = await self._sessions.create(
            agent=self.agent_id,
            workspace_subdir=workspace_subdir,
            title=title,
        )
        record.extra["depth"] = depth
        record.extra["conversation_id"] = uuid.uuid4().hex
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
        async with record.lock:
            record.state.status = "running"
            history: list[dict[str, str]] = record.extra.setdefault("history", [])
            history.append({"role": "user", "content": message})
            # Phase 2 implementation note: the OpenHands CLI does support
            # ``--resume <conversation_id>`` (see command-reference), but the
            # conversation database lives inside the *runtime sandbox* spawned
            # per CLI invocation — it is NOT preserved between our subprocess
            # spawns. Until we wire a persistent sandbox via a long-lived
            # ``openhands serve``-like backend, fall back to the same recap
            # strategy as clawcode-adapter: every turn re-sends the history as
            # a single task. The adapter remains compatible: external callers
            # see real multi-turn semantics; internally each turn is one fresh
            # headless run.
            recap = "\n\n".join(
                f"[{turn['role']}]\n{turn['content']}" for turn in history
            )
            result = await self.run_one_shot(
                task=recap,
                workspace_subdir=record.state.workspace_subdir,
                timeout_s=timeout_s,
                depth=depth,
            )
            history.append({"role": "assistant", "content": result.result_text})
            record.state.messages_seen += 1
            record.state.last_active_at = time.time()
            record.state.status = "ready" if result.ok else "error"
            record.state.logs_tail = result.logs_tail
            return session_state_for_api(record.state, record.extra)

    async def get_session(self, session_id: str) -> SessionState:
        record = await self._sessions.get(session_id)
        if record.state.agent != self.agent_id:
            raise HTTPException(status_code=404, detail="session not found")
        return session_state_for_api(record.state, record.extra)


def _iter_json_events(stdout: str):
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _extract_last_message(stdout: str) -> str:
    last = ""
    for event in _iter_json_events(stdout):
        if not isinstance(event, dict):
            continue
        # OpenHands JSONL: ``{"type": "action", "action": "message", "args": {"content": "..."}}``
        # and ``{"source": "agent", "message": "..."}`` are both seen across
        # 1.6 patch levels; cover both shapes.
        for key in ("message", "content", "text"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                last = value
        args = event.get("args")
        if isinstance(args, dict):
            for key in ("content", "message", "thought", "text"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    last = value
    if not last:
        return stdout.strip()
    return last.strip()


def _collect_files_changed(stdout: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for event in _iter_json_events(stdout):
        if not isinstance(event, dict):
            continue
        path = None
        if event.get("action") in {"write", "edit"}:
            path = event.get("path") or (event.get("args") or {}).get("path")
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        if not path:
            for key in ("path", "file_path"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    path = value
                    break
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def _build() -> Any:
    api_key = os.environ.get("OPENHANDS_ADAPTER_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "OPENHANDS_ADAPTER_API_KEY is empty — all /v1/* endpoints will be "
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
        service_name="openhands-adapter",
        title="OpenHands Adapter",
        description=(
            "OpenAPI + MCP SSE adapter for OpenHands headless. One "
            "`POST /v1/run` spawns a transient runtime sandbox via "
            "`docker.sock`, executes the task, and tears the sandbox down. "
            "Cycle guard via `X-Agent-Mesh-Depth` "
            f"(max_nested_agent_calls={max_nested})."
        ),
        skills_dir=skills_dir,
    )
    runner = OpenHandsRunner()
    return build_app(runner, config)


app = _build()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "8791")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
