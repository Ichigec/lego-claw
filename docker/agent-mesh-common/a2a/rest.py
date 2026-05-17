"""FastAPI router exposing the A2A REST binding (Section 11 of the spec).

Mount with::

    from a2a.rest import build_rest_router, TaskRunnerBridge

    bridge = TaskRunnerBridge(runner=runner, store=task_store)
    app.include_router(
        build_rest_router(bridge=bridge, store=task_store, bearer_dep=bearer_dep,
                          depth_dep=depth_dep),
        prefix="/a2a/v1",
    )

Endpoints (Section 11.3 of the A2A specification):

* ``POST /message:send`` — submit a new ``Message``; the bridge drives
  the task to a terminal state; non-blocking by default but supports
  ``configuration.blocking=true``.
* ``POST /message:stream`` — same, but stream events as SSE.
* ``GET /tasks`` — list with cursor pagination.
* ``GET /tasks/{task_id}`` — single task fetch (with optional
  ``historyLength`` / ``includeArtifacts`` query params).
* ``POST /tasks/{task_id}:cancel`` — cancel an in-flight task.
* ``POST /tasks/{task_id}:subscribe`` — resubscribe to an existing
  task's stream.
* ``POST/GET/DELETE /tasks/{task_id}/pushNotificationConfigs[/{config_id}]``.

Errors are emitted as RFC 7807 ``application/problem+json`` documents
(Section 5.4 maps each ``A2AError`` flavour to a problem ``type`` URI
and a sane HTTP status).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .task_store import (
    ListFilter,
    PushConfigNotFoundError,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskStore,
)
from .types import (
    Artifact,
    Message,
    PushNotificationConfig,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
    utc_now_iso,
)

logger = logging.getLogger("a2a.rest")


# ── runner-task bridge ──────────────────────────────────────────────────────
class _RunnerLike(Protocol):
    """Subset of :class:`agent_mesh_adapter.Runner` consumed by the bridge."""

    agent_id: str
    agent_label: str

    async def run_one_shot(
        self,
        *,
        task: str,
        workspace_subdir: str,
        timeout_s: int,
        depth: int,
    ) -> Any: ...


@dataclass
class TaskRunnerBridge:
    """Glue layer between the existing one-shot ``Runner`` and the A2A
    Task lifecycle.

    Each ``message:send`` creates a Task in the :class:`TaskStore` and
    spawns an asyncio task that:

    1. flips the status to ``WORKING``;
    2. extracts the user prompt from the latest :class:`Message`'s text
       parts (Section 4.1.6);
    3. invokes :meth:`Runner.run_one_shot`;
    4. appends a single ``Artifact`` carrying ``result_text`` and the
       runner metadata;
    5. records the terminal state (``COMPLETED`` or ``FAILED``).

    Cancellation is propagated through ``asyncio.Task.cancel()`` — the
    runner subprocess gets a SIGKILL via ``run_subprocess`` once the
    coroutine winds down.
    """

    runner: _RunnerLike
    store: TaskStore
    _running: dict[str, asyncio.Task[Any]] | None = None

    def __post_init__(self) -> None:
        if self._running is None:
            self._running = {}

    async def submit(
        self,
        message: Message,
        *,
        workspace_subdir: str = "",
        timeout_s: int = 600,
        depth: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        task = await self.store.create_task(
            context_id=message.context_id,
            initial_message=message,
            metadata=metadata,
        )
        bg = asyncio.create_task(
            self._drive(task.id, workspace_subdir, timeout_s, depth),
            name=f"a2a-task:{task.id[:8]}",
        )
        assert self._running is not None
        self._running[task.id] = bg

        def _cleanup(_t: asyncio.Task[Any]) -> None:
            assert self._running is not None
            self._running.pop(task.id, None)

        bg.add_done_callback(_cleanup)
        return task

    async def cancel(self, task_id: str) -> Task:
        assert self._running is not None
        bg = self._running.get(task_id)
        if bg is not None and not bg.done():
            bg.cancel()
        # ``cancel_task`` raises if already terminal; let it propagate.
        return await self.store.cancel_task(task_id)

    async def _drive(
        self,
        task_id: str,
        workspace_subdir: str,
        timeout_s: int,
        depth: int,
    ) -> None:
        try:
            await self.store.record_status(task_id, TaskState.WORKING)
            task = await self.store.get_task(task_id, history_length=1)
            prompt = self._extract_prompt(task)
            if not prompt:
                err = Message(
                    message_id=uuid.uuid4().hex,
                    role=Role.AGENT,
                    task_id=task_id,
                    context_id=task.context_id,
                    parts=[
                        TextPart(text="empty prompt: no text parts in message"),
                    ],
                )
                await self.store.record_status(
                    task_id, TaskState.REJECTED, message=err, final=True
                )
                return

            try:
                result = await self.runner.run_one_shot(
                    task=prompt,
                    workspace_subdir=workspace_subdir,
                    timeout_s=timeout_s,
                    depth=depth,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — surface as FAILED
                logger.exception("runner failure for task=%s", task_id)
                err_msg = Message(
                    message_id=uuid.uuid4().hex,
                    role=Role.AGENT,
                    task_id=task_id,
                    context_id=task.context_id,
                    parts=[TextPart(text=f"runner error: {exc}")],
                )
                await self.store.record_status(
                    task_id,
                    TaskState.FAILED,
                    message=err_msg,
                    final=True,
                )
                return

            result_text = getattr(result, "result_text", "") or ""
            ok = bool(getattr(result, "ok", False))
            artifact = Artifact(
                artifact_id=uuid.uuid4().hex,
                name="result",
                description="Final response from the headless runner.",
                parts=[TextPart(text=result_text)] if result_text else [],
                metadata={
                    "filesChanged": list(getattr(result, "files_changed", []) or []),
                    "exitCode": getattr(result, "exit_code", None),
                    "durationS": getattr(result, "duration_s", 0.0),
                    "logsTail": getattr(result, "logs_tail", "")[-2048:],
                },
            )
            await self.store.append_artifact(task_id, artifact, last_chunk=True)

            if ok:
                done_msg = Message(
                    message_id=uuid.uuid4().hex,
                    role=Role.AGENT,
                    task_id=task_id,
                    context_id=task.context_id,
                    parts=[TextPart(text=result_text)] if result_text else [],
                )
                await self.store.record_status(
                    task_id,
                    TaskState.COMPLETED,
                    message=done_msg,
                    final=True,
                )
            else:
                err = getattr(result, "error", None) or "runner reported failure"
                err_msg = Message(
                    message_id=uuid.uuid4().hex,
                    role=Role.AGENT,
                    task_id=task_id,
                    context_id=task.context_id,
                    parts=[TextPart(text=str(err))],
                )
                await self.store.record_status(
                    task_id,
                    TaskState.FAILED,
                    message=err_msg,
                    final=True,
                )
        except asyncio.CancelledError:
            try:
                # Best-effort terminal write; ignore if already terminal.
                await self.store.record_status(
                    task_id, TaskState.CANCELED, final=True
                )
            except Exception:  # noqa: BLE001
                pass
            raise

    @staticmethod
    def _extract_prompt(task: Task) -> str:
        if not task.history:
            return ""
        last = task.history[-1]
        return "\n\n".join(
            p.text for p in last.parts if isinstance(p, TextPart) and p.text
        ).strip()


# ── request / response models ───────────────────────────────────────────────
class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


class MessageSendConfiguration(_CamelModel):
    """Section 4.5 — knobs for ``message:send``."""

    accepted_output_modes: list[str] = Field(default_factory=list)
    push_notification_config: PushNotificationConfig | None = None
    history_length: int | None = None
    blocking: bool = False


class MessageSendParams(_CamelModel):
    message: Message
    configuration: MessageSendConfiguration = Field(
        default_factory=MessageSendConfiguration
    )
    metadata: dict[str, Any] | None = None


class TaskListResponse(_CamelModel):
    tasks: list[Task]
    next_page_token: str | None = None


# ── RFC 7807 problem documents ──────────────────────────────────────────────
A2A_PROBLEM_BASE = "https://a2aproject.dev/problems/"


@dataclass
class A2AProblem:
    """Concrete problem document (Section 5.4)."""

    type: str
    title: str
    status: int
    detail: str | None = None

    def to_dict(self, instance: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        return body

    def response(self, instance: str | None = None) -> JSONResponse:
        return JSONResponse(
            content=self.to_dict(instance),
            status_code=self.status,
            media_type="application/problem+json",
        )


def _problem(
    *, type_: str, title: str, status: int, detail: str | None = None
) -> A2AProblem:
    return A2AProblem(
        type=A2A_PROBLEM_BASE + type_,
        title=title,
        status=status,
        detail=detail,
    )


def _problem_for(exc: Exception) -> A2AProblem:
    if isinstance(exc, TaskNotFoundError):
        return _problem(
            type_="task-not-found",
            title="Task not found",
            status=404,
            detail=str(exc),
        )
    if isinstance(exc, PushConfigNotFoundError):
        return _problem(
            type_="push-notification-config-not-found",
            title="Push notification config not found",
            status=404,
            detail=str(exc),
        )
    if isinstance(exc, TaskNotCancelableError):
        return _problem(
            type_="task-not-cancelable",
            title="Task is in a terminal state and cannot be cancelled",
            status=409,
            detail=str(exc),
        )
    if isinstance(exc, ValueError):
        return _problem(
            type_="invalid-params",
            title="Invalid parameters",
            status=400,
            detail=str(exc),
        )
    return _problem(
        type_="internal-error",
        title="Internal error",
        status=500,
        detail=str(exc),
    )


def install_problem_handlers(app: Any) -> None:
    """Register exception handlers that emit ``application/problem+json``."""
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):
        return

    @app.exception_handler(TaskNotFoundError)
    async def _h_404(request: Request, exc: TaskNotFoundError) -> Response:
        return _problem_for(exc).response(str(request.url.path))

    @app.exception_handler(PushConfigNotFoundError)
    async def _h_push_404(
        request: Request, exc: PushConfigNotFoundError
    ) -> Response:
        return _problem_for(exc).response(str(request.url.path))

    @app.exception_handler(TaskNotCancelableError)
    async def _h_409(
        request: Request, exc: TaskNotCancelableError
    ) -> Response:
        return _problem_for(exc).response(str(request.url.path))


# ── SSE helpers ─────────────────────────────────────────────────────────────
def _sse_event(payload: BaseModel) -> bytes:
    js = payload.model_dump_json(by_alias=True, exclude_none=True)
    return f"data: {js}\n\n".encode()


async def _sse_stream(
    events: AsyncIterator[Any],
) -> AsyncIterator[bytes]:
    try:
        async for ev in events:
            if not isinstance(ev, BaseModel):
                continue
            yield _sse_event(ev)
    except asyncio.CancelledError:
        # Client disconnected mid-stream — stop quietly.
        return
    except Exception:  # noqa: BLE001
        logger.exception("SSE stream error")
        # Per Section 11.4, end the stream silently on internal failure.
        return


# ── filter parsers ──────────────────────────────────────────────────────────
def _parse_states(raw: str | None) -> tuple[TaskState, ...] | None:
    if not raw:
        return None
    out: list[TaskState] = []
    for token in raw.split(","):
        t = token.strip()
        if t:
            out.append(TaskState.coerce(t))
    return tuple(out) or None


# ── router builder ──────────────────────────────────────────────────────────
def build_rest_router(
    *,
    bridge: TaskRunnerBridge,
    store: TaskStore,
    bearer_dep: Callable[..., Any] | None = None,
    depth_dep: Callable[..., Any] | None = None,
    default_timeout_s: int = 600,
    blocking_wait_s: float = 30.0,
) -> APIRouter:
    """Return the ``/a2a/v1`` :class:`APIRouter`.

    ``bearer_dep`` and ``depth_dep`` are reused from
    :mod:`agent_mesh_adapter`; they default to no-ops if ``None``.

    ``blocking_wait_s`` caps the wait time for ``configuration.blocking
    = true`` requests; clients that exceed the budget receive the
    current Task snapshot in whatever state it is in.
    """
    deps: list[Any] = []
    if bearer_dep is not None:
        deps.append(Depends(bearer_dep))

    def _depth(*, request: Request) -> int:
        if depth_dep is None:
            return 0
        try:
            return depth_dep(request.headers.get("x-agent-mesh-depth"))
        except TypeError:
            # FastAPI-style dependency that takes a Header param.
            return 0

    router = APIRouter(default_response_class=JSONResponse)

    # ── message:send ────────────────────────────────────────────────────
    @router.post(
        "/message:send",
        dependencies=deps,
        summary="Submit a new Message and (optionally) wait for terminal state.",
    )
    async def message_send(
        params: MessageSendParams = Body(...),
        request: Request = None,  # type: ignore[assignment]
    ) -> JSONResponse:
        depth = _depth(request=request)
        ws = (params.metadata or {}).get("workspaceSubdir", "")
        timeout = int((params.metadata or {}).get("timeoutS", default_timeout_s))
        task = await bridge.submit(
            params.message,
            workspace_subdir=str(ws),
            timeout_s=timeout,
            depth=depth,
            metadata=params.metadata,
        )
        if params.configuration.push_notification_config is not None:
            await store.add_push_config(
                task.id, params.configuration.push_notification_config
            )
        if params.configuration.blocking:
            task = await _wait_terminal(store, task.id, blocking_wait_s)

        return JSONResponse(
            task.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    # ── message:stream ──────────────────────────────────────────────────
    @router.post(
        "/message:stream",
        dependencies=deps,
        summary="Submit a new Message and stream events as SSE.",
        response_class=StreamingResponse,
    )
    async def message_stream(
        params: MessageSendParams = Body(...),
        request: Request = None,  # type: ignore[assignment]
    ) -> StreamingResponse:
        depth = _depth(request=request)
        ws = (params.metadata or {}).get("workspaceSubdir", "")
        timeout = int((params.metadata or {}).get("timeoutS", default_timeout_s))
        task = await bridge.submit(
            params.message,
            workspace_subdir=str(ws),
            timeout_s=timeout,
            depth=depth,
            metadata=params.metadata,
        )
        if params.configuration.push_notification_config is not None:
            await store.add_push_config(
                task.id, params.configuration.push_notification_config
            )
        return StreamingResponse(
            _sse_stream(store.subscribe(task.id)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── tasks list / get ────────────────────────────────────────────────
    @router.get(
        "/tasks",
        dependencies=deps,
        summary="List tasks (cursor-paginated, Section 11.5).",
    )
    async def list_tasks(
        context_id: str | None = Query(None, alias="contextId"),
        status: str | None = Query(None, description="Comma-separated TaskStates"),
        page_size: int = Query(50, alias="pageSize", ge=1, le=500),
        page_token: str | None = Query(None, alias="pageToken"),
        history_length: int | None = Query(None, alias="historyLength"),
        include_artifacts: bool = Query(True, alias="includeArtifacts"),
        status_timestamp_after: str | None = Query(
            None, alias="statusTimestampAfter"
        ),
    ) -> JSONResponse:
        page = await store.list_tasks(
            ListFilter(
                context_id=context_id,
                states=_parse_states(status),
                page_size=page_size,
                page_token=page_token,
                history_length=history_length,
                include_artifacts=include_artifacts,
                status_timestamp_after=status_timestamp_after,
            )
        )
        body = TaskListResponse(
            tasks=page.tasks, next_page_token=page.next_page_token
        )
        return JSONResponse(
            body.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @router.get(
        "/tasks/{task_id}",
        dependencies=deps,
        summary="Fetch a single Task.",
    )
    async def get_task(
        task_id: str,
        history_length: int | None = Query(None, alias="historyLength"),
        include_artifacts: bool = Query(True, alias="includeArtifacts"),
    ) -> JSONResponse:
        task = await store.get_task(
            task_id,
            history_length=history_length,
            include_artifacts=include_artifacts,
        )
        return JSONResponse(
            task.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @router.post(
        "/tasks/{task_id}:cancel",
        dependencies=deps,
        summary="Cancel an in-flight Task (Section 11.6).",
    )
    async def cancel_task(task_id: str) -> JSONResponse:
        task = await bridge.cancel(task_id)
        return JSONResponse(
            task.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @router.post(
        "/tasks/{task_id}:subscribe",
        dependencies=deps,
        summary="Resubscribe to an existing Task's event stream (SSE).",
        response_class=StreamingResponse,
    )
    async def subscribe_task(task_id: str) -> StreamingResponse:
        # Force-error if the task is missing — surfaces as 404 problem+json.
        await store.get_task(task_id, history_length=0, include_artifacts=False)
        return StreamingResponse(
            _sse_stream(store.subscribe(task_id)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── push notification configs ───────────────────────────────────────
    @router.post(
        "/tasks/{task_id}/pushNotificationConfigs",
        dependencies=deps,
        summary="Register a push notification webhook for a Task.",
    )
    async def add_push_config(
        task_id: str,
        config: PushNotificationConfig = Body(...),
    ) -> JSONResponse:
        await store.get_task(task_id, history_length=0, include_artifacts=False)
        saved = await store.add_push_config(task_id, config)
        return JSONResponse(
            saved.model_dump(mode="json", by_alias=True, exclude_none=True),
            status_code=201,
        )

    @router.get(
        "/tasks/{task_id}/pushNotificationConfigs",
        dependencies=deps,
        summary="List push notification configs for a Task.",
    )
    async def list_push_configs(task_id: str) -> JSONResponse:
        await store.get_task(task_id, history_length=0, include_artifacts=False)
        cfgs = await store.list_push_configs(task_id)
        return JSONResponse(
            [c.model_dump(mode="json", by_alias=True, exclude_none=True) for c in cfgs]
        )

    @router.get(
        "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
        dependencies=deps,
        summary="Fetch a single push notification config.",
    )
    async def get_push_config(task_id: str, config_id: str) -> JSONResponse:
        cfg = await store.get_push_config(task_id, config_id)
        return JSONResponse(
            cfg.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    @router.delete(
        "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
        dependencies=deps,
        summary="Delete a push notification config.",
        status_code=204,
    )
    async def delete_push_config(task_id: str, config_id: str) -> Response:
        await store.delete_push_config(task_id, config_id)
        return Response(status_code=204)

    return router


# ── helpers ─────────────────────────────────────────────────────────────────
async def _wait_terminal(
    store: TaskStore, task_id: str, timeout_s: float
) -> Task:
    """Poll subscribe(stream) until terminal state or timeout."""
    deadline = asyncio.get_event_loop().time() + max(0.1, timeout_s)
    last: Task | None = None

    async def _consume() -> Task:
        nonlocal last
        async for ev in store.subscribe(task_id):
            if isinstance(ev, Task):
                last = ev
                if ev.status.state.is_terminal:
                    return ev
            elif isinstance(ev, TaskStatusUpdateEvent) and ev.final:
                return await store.get_task(task_id)
        return await store.get_task(task_id)

    try:
        return await asyncio.wait_for(
            _consume(), timeout=max(0.1, deadline - asyncio.get_event_loop().time())
        )
    except asyncio.TimeoutError:
        return last or await store.get_task(task_id)


__all__ = [
    "TaskRunnerBridge",
    "MessageSendConfiguration",
    "MessageSendParams",
    "TaskListResponse",
    "A2AProblem",
    "build_rest_router",
    "install_problem_handlers",
]
