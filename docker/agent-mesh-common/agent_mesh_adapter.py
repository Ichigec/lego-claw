"""Reusable FastAPI surface for agent-mesh adapters (Track 1 of the plan).

Each adapter (clawcode, openhands) provides its own ``Runner`` implementation
that knows how to execute a one-shot task and how to keep a persistent session
alive. This module ties the runner to a uniform HTTP contract:

* ``POST /v1/run`` — one-shot task; returns a structured ``RunResult``.
* ``POST /v1/sessions`` / ``POST /v1/sessions/{id}/messages`` /
  ``GET /v1/sessions/{id}`` — phase-2 multi-turn delegation. These endpoints
  intentionally stay out of the generated OpenAPI schema: Open WebUI treats
  path templates like ``{session_id}`` as callable tools and may call them
  literally. Tool clients should use ``POST /v1/run``.
* ``GET /openapi.json`` — auto-generated, consumed by OpenWebUI as a tool
  server (bearer-auth, same pattern as ``searchbox``/``fsbox``).
* ``GET /sse`` (+ ``POST /messages``) — MCP transport for Claw / OpenHands
  cross-wiring (LLM clients see the adapter as just another MCP server).
* ``GET /healthz`` — unauthenticated liveness probe for docker / stack-start.

The two non-trivial mechanisms shared by both adapters live here:

1. **Bearer auth**. ``AGENT_MESH_API_KEY`` gates every ``/v1/*`` route and
   ``/openapi.json`` (so the OpenWebUI tool-server bearer behaves the same as
   for ``searchbox``). MCP ``/sse`` and ``/healthz`` are intentionally open
   because they are only reachable on the compose-internal ``llm-stack-net``
   (plus a loopback host-port for OpenHands runtime sandboxes that have to use
   ``host.docker.internal``).
2. **Cycle guard**. Every adapter call carries an ``X-Agent-Mesh-Depth``
   header. We reject when the incoming depth is ≥ ``MAX_NESTED_AGENT_CALLS``
   (default ``1``) so an LLM cannot recursively spawn agents that spawn agents.
   The runner gets the incremented depth and is expected to propagate it to
   any subagent it invokes downstream.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from skills_loader import (
        Skill,
        compose_system_prompt,
        default_skills_dir,
        load_skills,
        merge_mcp_servers,
        skills_version,
    )
except ImportError:  # tests may import this module without skills_loader
    Skill = None  # type: ignore[assignment]
    compose_system_prompt = None  # type: ignore[assignment]
    default_skills_dir = None  # type: ignore[assignment]
    load_skills = None  # type: ignore[assignment]
    merge_mcp_servers = None  # type: ignore[assignment]
    skills_version = None  # type: ignore[assignment]

logger = logging.getLogger("agent_mesh_adapter")


# ── Telemetry ────────────────────────────────────────────────────────────────
def init_otel(service_name: str) -> None:
    """Wire OTLP → Phoenix if the SDK is present. No-op otherwise."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set; tracing disabled")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("opentelemetry-* not installed; tracing disabled")
        return

    # Phoenix groups traces by `openinference.project.name` (and accepts
    # `phoenix.project.name` as an alias). Setting both makes the adapter
    # spans land in the same project as LiteLLM's success_callback. The
    # default value matches docker/litellm/config.yaml.
    project = os.environ.get("PHOENIX_PROJECT_NAME", "qwen3.6-heretic")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "agent-mesh",
            "openinference.project.name": project,
            "phoenix.project.name": project,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter_endpoint = endpoint.rstrip("/")
    if not exporter_endpoint.endswith("/v1/traces"):
        exporter_endpoint = f"{exporter_endpoint}/v1/traces"
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=exporter_endpoint))
    )
    trace.set_tracer_provider(provider)
    logger.info(
        "tracing enabled; otlp endpoint=%s project=%s",
        exporter_endpoint,
        project,
    )


def _get_tracer(name: str):
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer(name)


# ── HTTP contract ────────────────────────────────────────────────────────────
class RunRequest(BaseModel):
    """One-shot delegation payload."""

    task: str = Field(
        ...,
        min_length=1,
        max_length=64_000,
        description=(
            "Task as a plain-text instruction. Headless agents do not have a "
            "system-prompt slot beyond this string — be self-contained."
        ),
    )
    workspace_subdir: str = Field(
        default="",
        max_length=512,
        description=(
            "Optional subdirectory of the shared agent sandbox to use as the "
            "agent's CWD. Empty = repo-wide workspace root (/workspace/project)."
        ),
    )
    timeout_s: int = Field(
        default=600,
        ge=10,
        le=3600,
        description="Hard wall-clock budget for the run, in seconds.",
    )


class RunResult(BaseModel):
    ok: bool
    result_text: str = ""
    files_changed: list[str] = []
    logs_tail: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    depth: int = 0
    error: str | None = None


class SessionCreateRequest(BaseModel):
    workspace_subdir: str = Field(default="", max_length=512)
    title: str = Field(default="", max_length=200)


class SessionMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=64_000)
    timeout_s: int = Field(default=300, ge=10, le=3600)


class SessionState(BaseModel):
    id: str
    agent: str
    workspace_subdir: str
    title: str
    created_at: float
    last_active_at: float
    status: str
    messages_seen: int
    logs_tail: str = ""
    last_user_message: str = Field(
        default="",
        description=(
            "Last user message in this session (from in-memory history). "
            "Populated on GET /v1/sessions/{id} and after POST .../messages."
        ),
    )
    last_assistant_message: str = Field(
        default="",
        description=(
            "Last agent reply text for this session — what tools/Open WebUI "
            "should surface as the visible answer."
        ),
    )


def session_state_for_api(state: SessionState, extra: dict[str, Any]) -> SessionState:
    """Attach last user/assistant lines from runner ``extra['history']``."""
    history = extra.get("history")
    last_user = ""
    last_assistant = ""
    if isinstance(history, list):
        for turn in history:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            raw = turn.get("content")
            content = raw.strip() if isinstance(raw, str) else ""
            if role == "user":
                last_user = content
            elif role == "assistant":
                last_assistant = content
    return state.model_copy(
        update={
            "last_user_message": last_user,
            "last_assistant_message": last_assistant,
        }
    )


# ── Runner protocol ──────────────────────────────────────────────────────────
class Runner(Protocol):
    """Adapter-specific execution backend.

    The ``depth`` argument is the *post-increment* nesting depth: an LLM that
    talks to this adapter directly will call us with ``X-Agent-Mesh-Depth: 0``
    (or no header), and the runner sees ``depth=1``. If the runner spawns
    further delegations downstream it MUST forward this number in the same
    header so the guard chains correctly.
    """

    agent_id: str
    agent_label: str

    async def run_one_shot(
        self,
        *,
        task: str,
        workspace_subdir: str,
        timeout_s: int,
        depth: int,
    ) -> RunResult: ...

    async def create_session(
        self,
        *,
        workspace_subdir: str,
        title: str,
        depth: int,
    ) -> SessionState: ...

    async def post_message(
        self,
        *,
        session_id: str,
        message: str,
        timeout_s: int,
        depth: int,
    ) -> SessionState: ...

    async def get_session(self, session_id: str) -> SessionState: ...


# ── Cycle guard ──────────────────────────────────────────────────────────────
def _parse_depth(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value.strip()))
    except ValueError:
        return 0


def make_depth_dep(
    max_nested: int,
) -> Callable[[str | None], int]:
    def _dep(
        x_agent_mesh_depth: str | None = Header(default=None),
    ) -> int:
        depth = _parse_depth(x_agent_mesh_depth) + 1
        if depth > max_nested:
            raise HTTPException(
                status_code=429,
                detail=(
                    "agent-mesh nesting limit exceeded: incoming depth="
                    f"{depth - 1}, max_nested_agent_calls={max_nested}. "
                    "Refusing to delegate further to avoid recursive loops."
                ),
            )
        return depth

    return _dep


MESH_DEPTH_HEADER = "X-Agent-Mesh-Depth"


class MeshDepthEchoMiddleware(BaseHTTPMiddleware):
    """Echo the observed depth back in the response.

    Useful for debugging tool-chains (curl can see exactly what the adapter
    parsed). Does NOT enforce the cycle guard — that is done inside each
    `/v1/*` endpoint via the ``Depends(depth_dep)`` parameter so 429
    responses include a structured `detail` payload.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = _parse_depth(request.headers.get(MESH_DEPTH_HEADER))
        response = await call_next(request)
        # Report the *outgoing* depth (incoming + 1), which is what a
        # downstream adapter sees if the caller propagates the header.
        response.headers[MESH_DEPTH_HEADER] = str(incoming + 1)
        return response


# ── Bearer auth ──────────────────────────────────────────────────────────────
def make_bearer_dep(api_key: str) -> Callable[[str | None], None]:
    def _dep(authorization: str | None = Header(default=None)) -> None:
        if not api_key:
            return
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Bearer token required")
        token = authorization.split(" ", 1)[1].strip()
        if token != api_key:
            raise HTTPException(status_code=401, detail="Invalid bearer token")

    return _dep


# ── MCP SSE shim ─────────────────────────────────────────────────────────────
def _build_mcp_app(runner: Runner, max_nested: int):
    """Construct an MCP server exposing ``run_<agent>`` and ``send_message``.

    Uses ``mcp.server.fastmcp.FastMCP`` if available. Falls back to ``None`` if
    the MCP SDK is not installed — in that case the adapter still works as an
    OpenAPI tool server but the ``/sse`` route returns 503.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.warning(
            "mcp SDK not installed; /sse endpoint will be unavailable"
        )
        return None

    mcp = FastMCP(f"{runner.agent_id}-adapter")

    tool_name = f"run_{runner.agent_id}"
    tool_desc = (
        f"Delegate a self-contained coding task to the {runner.agent_label} "
        "agent and wait for the final answer or diff. ONE call = ONE task; "
        "the agent runs headless without any further user interaction. Do NOT "
        "use this tool if you can solve the task yourself; do NOT use it from "
        "inside the agent you are calling (self-delegation is blocked by the "
        f"cycle guard, max_nested_agent_calls={max_nested})."
    )

    @mcp.tool(name=tool_name, description=tool_desc)
    async def _tool_run(
        task: str,
        workspace_subdir: str = "",
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        depth_dep = make_depth_dep(max_nested)
        depth = depth_dep(None)
        result = await runner.run_one_shot(
            task=task,
            workspace_subdir=workspace_subdir,
            timeout_s=timeout_s,
            depth=depth,
        )
        return result.model_dump()

    return mcp


# ── Skills bundle ────────────────────────────────────────────────────────────
@dataclass
class SkillsBundle:
    """Snapshot of skills loaded for an adapter.

    Runners receive this via :meth:`Runner.apply_skills` whenever the
    adapter starts up or when ``POST /admin/reload`` re-reads ``.ai/``.
    Adapters that do not care about skills can simply not implement
    ``apply_skills``; the legacy ``/v1/run`` path then keeps its
    one-prompt-per-call behaviour with no system prompt injected.
    """

    skills: list[Any] = field(default_factory=list)
    system_prompt: str = ""
    mcp_servers: list[Any] = field(default_factory=list)
    version: str = ""


def load_skills_bundle(
    skills_dir: str | None,
    agent_id: str,
    base_mcp: list[Any] | None = None,
) -> SkillsBundle:
    """Read the on-disk skill catalogue and build a :class:`SkillsBundle`.

    Empty bundle on missing directory or missing ``skills_loader`` deps —
    this keeps adapters fully backward-compatible when ``.ai/`` is not
    mounted.
    """
    if load_skills is None or skills_dir is None:
        return SkillsBundle()
    skills = load_skills(skills_dir, agent_id) or []
    if not skills:
        return SkillsBundle()
    prompt = compose_system_prompt(skills_dir, agent_id, skills) if compose_system_prompt else ""
    merged = merge_mcp_servers(skills, base_mcp or []) if merge_mcp_servers else list(base_mcp or [])
    version = skills_version(skills) if skills_version else ""
    return SkillsBundle(
        skills=skills,
        system_prompt=prompt,
        mcp_servers=merged,
        version=version,
    )


# ── App factory ──────────────────────────────────────────────────────────────
@dataclass
class AdapterConfig:
    api_key: str
    max_nested_agent_calls: int = 1
    service_name: str = "agent-mesh-adapter"
    title: str = "Agent Mesh Adapter"
    description: str = ""
    skills_dir: str | None = None
    base_mcp_servers: list[Any] = field(default_factory=list)


def build_app(runner: Runner, config: AdapterConfig) -> FastAPI:
    init_otel(config.service_name)
    tracer = _get_tracer(config.service_name)

    bearer_dep = make_bearer_dep(config.api_key)
    depth_dep = make_depth_dep(config.max_nested_agent_calls)

    description = config.description or (
        f"OpenAPI tool server for the {runner.agent_label} agent. "
        f"One call = one headless task. max_nested_agent_calls="
        f"{config.max_nested_agent_calls}."
    )

    skills_dir = config.skills_dir
    if skills_dir is None and default_skills_dir is not None:
        skills_dir = default_skills_dir()

    bundle_state: dict[str, SkillsBundle] = {
        "current": load_skills_bundle(
            skills_dir, runner.agent_id, config.base_mcp_servers
        ),
    }

    def _apply_to_runner(bundle: SkillsBundle) -> None:
        apply = getattr(runner, "apply_skills", None)
        if callable(apply):
            try:
                apply(bundle)
            except Exception as exc:
                logger.warning(
                    "%s.apply_skills failed: %s",
                    type(runner).__name__,
                    exc,
                )

    _apply_to_runner(bundle_state["current"])
    if bundle_state["current"].skills:
        logger.info(
            "%s loaded %d skills (version=%s) from %s",
            config.service_name,
            len(bundle_state["current"].skills),
            bundle_state["current"].version,
            skills_dir,
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logger.info(
            "starting %s adapter; agent=%s max_nested=%d",
            config.service_name,
            runner.agent_id,
            config.max_nested_agent_calls,
        )
        yield
        logger.info("stopping %s adapter", config.service_name)

    app = FastAPI(
        title=config.title,
        version="1.0.0",
        description=description,
        lifespan=lifespan,
    )
    app.add_middleware(MeshDepthEchoMiddleware)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "agent": runner.agent_id,
            "max_nested_agent_calls": config.max_nested_agent_calls,
        }

    run_summary = (
        f"Delegate one self-contained task to the {runner.agent_label} agent."
    )
    run_description = (
        f"Headless one-shot delegation to **{runner.agent_label}**. The agent "
        "runs without any further user interaction and returns the final "
        "answer or diff in `result_text`. Use ONLY when the task is outside "
        "your own capabilities — never call yourself (the cycle guard blocks "
        "self-delegation via the `X-Agent-Mesh-Depth` header)."
    )

    @app.post(
        "/v1/run",
        response_model=RunResult,
        summary=run_summary,
        description=run_description,
        dependencies=[Depends(bearer_dep)],
    )
    async def v1_run(
        payload: RunRequest,
        depth: int = Depends(depth_dep),
    ) -> RunResult:
        started = time.time()
        if tracer is not None:
            with tracer.start_as_current_span(f"agent_mesh.run.{runner.agent_id}") as span:
                span.set_attribute("agent.id", runner.agent_id)
                span.set_attribute("agent.depth", depth)
                span.set_attribute("task.length", len(payload.task))
                result = await runner.run_one_shot(
                    task=payload.task,
                    workspace_subdir=payload.workspace_subdir,
                    timeout_s=payload.timeout_s,
                    depth=depth,
                )
                span.set_attribute("run.ok", result.ok)
                if result.exit_code is not None:
                    span.set_attribute("run.exit_code", result.exit_code)
        else:
            result = await runner.run_one_shot(
                task=payload.task,
                workspace_subdir=payload.workspace_subdir,
                timeout_s=payload.timeout_s,
                depth=depth,
            )
        if not result.duration_s:
            result.duration_s = round(time.time() - started, 3)
        result.depth = depth
        return result

    @app.post(
        "/v1/sessions",
        response_model=SessionState,
        summary=f"Create a persistent session with {runner.agent_label}",
        dependencies=[Depends(bearer_dep)],
        include_in_schema=False,
    )
    async def v1_sessions_create(
        payload: SessionCreateRequest,
        depth: int = Depends(depth_dep),
    ) -> SessionState:
        if tracer is not None:
            with tracer.start_as_current_span(
                f"agent_mesh.sessions.create.{runner.agent_id}"
            ) as span:
                span.set_attribute("agent.id", runner.agent_id)
                span.set_attribute("agent.depth", depth)
                return await runner.create_session(
                    workspace_subdir=payload.workspace_subdir,
                    title=payload.title,
                    depth=depth,
                )
        return await runner.create_session(
            workspace_subdir=payload.workspace_subdir,
            title=payload.title,
            depth=depth,
        )

    @app.post(
        "/v1/sessions/{session_id}/messages",
        response_model=SessionState,
        summary=f"Send a follow-up message to a {runner.agent_label} session",
        dependencies=[Depends(bearer_dep)],
        include_in_schema=False,
    )
    async def v1_sessions_message(
        session_id: str,
        payload: SessionMessageRequest,
        depth: int = Depends(depth_dep),
    ) -> SessionState:
        if tracer is not None:
            with tracer.start_as_current_span(
                f"agent_mesh.sessions.message.{runner.agent_id}"
            ) as span:
                span.set_attribute("agent.id", runner.agent_id)
                span.set_attribute("agent.depth", depth)
                span.set_attribute("session.id", session_id)
                span.set_attribute("message.length", len(payload.message))
                state = await runner.post_message(
                    session_id=session_id,
                    message=payload.message,
                    timeout_s=payload.timeout_s,
                    depth=depth,
                )
                span.set_attribute("session.status", state.status)
                return state
        return await runner.post_message(
            session_id=session_id,
            message=payload.message,
            timeout_s=payload.timeout_s,
            depth=depth,
        )

    @app.get(
        "/v1/sessions/{session_id}",
        response_model=SessionState,
        summary=f"Inspect a {runner.agent_label} session",
        dependencies=[Depends(bearer_dep)],
        include_in_schema=False,
    )
    async def v1_sessions_get(session_id: str) -> SessionState:
        return await runner.get_session(session_id)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        bundle = bundle_state["current"]
        return {
            "service": config.service_name,
            "agent": runner.agent_id,
            "openapi": "/openapi.json",
            "mcp_sse": "/sse",
            "max_nested_agent_calls": config.max_nested_agent_calls,
            "skills_version": bundle.version,
            "skills_count": len(bundle.skills),
        }

    @app.get(
        "/v1/skills",
        summary=f"List skills attached to {runner.agent_label}",
        dependencies=[Depends(bearer_dep)],
    )
    async def v1_skills() -> dict[str, Any]:
        bundle = bundle_state["current"]
        return {
            "agent_id": runner.agent_id,
            "version": bundle.version,
            "skills": [
                s.to_agent_skill() if hasattr(s, "to_agent_skill") else s
                for s in bundle.skills
            ],
            "mcp_servers": list(bundle.mcp_servers),
        }

    @app.post(
        "/admin/reload",
        summary="Reload skills from disk and refresh the Agent Card",
        dependencies=[Depends(bearer_dep)],
        include_in_schema=False,
    )
    async def admin_reload() -> dict[str, Any]:
        new_bundle = load_skills_bundle(
            skills_dir, runner.agent_id, config.base_mcp_servers
        )
        bundle_state["current"] = new_bundle
        _apply_to_runner(new_bundle)
        logger.info(
            "%s skills reloaded; %d skills version=%s",
            config.service_name,
            len(new_bundle.skills),
            new_bundle.version,
        )
        return {
            "ok": True,
            "skills_count": len(new_bundle.skills),
            "skills_version": new_bundle.version,
        }

    @app.middleware("http")
    async def _bearer_on_openapi(request: Request, call_next):
        if request.url.path == "/openapi.json" and config.api_key:
            try:
                bearer_dep(request.headers.get("authorization"))
            except HTTPException as exc:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return await call_next(request)

    mcp = _build_mcp_app(runner, config.max_nested_agent_calls)
    if mcp is not None:
        try:
            sse_app = mcp.sse_app()
            app.mount("/", sse_app)
        except Exception as exc:
            logger.warning("failed to mount MCP SSE: %s", exc)

    return app


# ── Helpers for runners ──────────────────────────────────────────────────────
@dataclass
class _SessionRecord:
    state: SessionState
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    extra: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Tiny in-memory session registry shared by runners."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}
        self._global_lock = asyncio.Lock()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    async def create(
        self,
        *,
        agent: str,
        workspace_subdir: str,
        title: str,
    ) -> _SessionRecord:
        sid = self.new_id()
        now = time.time()
        state = SessionState(
            id=sid,
            agent=agent,
            workspace_subdir=workspace_subdir,
            title=title or f"{agent}-{sid[:8]}",
            created_at=now,
            last_active_at=now,
            status="ready",
            messages_seen=0,
        )
        record = _SessionRecord(state=state)
        async with self._global_lock:
            self._sessions[sid] = record
        return record

    async def get(self, session_id: str) -> _SessionRecord:
        async with self._global_lock:
            rec = self._sessions.get(session_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="session not found")
        return rec

    async def drop(self, session_id: str) -> None:
        async with self._global_lock:
            self._sessions.pop(session_id, None)


async def run_subprocess(
    cmd: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int,
    stdin_data: str | None = None,
    tail_bytes: int = 16_384,
) -> tuple[int, str, str]:
    """Spawn a subprocess, enforce timeout, return (rc, stdout, stderr_tail)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_data.encode("utf-8") if stdin_data else None),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        return -1, "", f"timeout after {timeout_s}s"
    text_out = stdout.decode("utf-8", errors="replace") if stdout else ""
    text_err = stderr.decode("utf-8", errors="replace") if stderr else ""
    if tail_bytes and len(text_err) > tail_bytes:
        text_err = "...\n" + text_err[-tail_bytes:]
    return proc.returncode if proc.returncode is not None else -1, text_out, text_err
