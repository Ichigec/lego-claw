"""Shared A2A method handlers (Section 9.4 / 11.3 / 10).

The three bindings (REST, JSON-RPC, gRPC) all share the same business logic
— only the transport envelope changes.  Putting the logic here keeps the
bindings dumb (parse → call → render).

The handlers are typed against :class:`InMemoryTaskStore` for now, but the
contract is purely method-level (``get_task``/``create_task``/...), so a
Postgres-backed store can substitute without changes.

Adapters provide a :class:`RunnerLike` that knows how to advance a
``Task`` toward a terminal state — usually by calling the underlying
agent CLI.  The runner is invoked through ``submit_message``; the
handlers themselves never know what the agent does.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Protocol

from .task_store import InMemoryTaskStore
from .types import (
    A2AError,
    Message,
    PushNotificationConfig,
    Task,
    TaskState,
)

logger = logging.getLogger("agent_mesh_adapter.a2a")


class RunnerLike(Protocol):
    """Hook used by ``submit_message`` to spawn the actual agent work.

    The runner is responsible for transitioning the task through
    ``WORKING`` → ``COMPLETED|FAILED|...`` and for appending any artifacts
    via the task store.  ``run_task`` returns once the task reaches a
    terminal **or interrupted** (``INPUT_REQUIRED``/``AUTH_REQUIRED``)
    state.  Adapters typically wire this to ``Runner.run_one_shot``.
    """

    agent_id: str
    agent_label: str

    async def run_task(self, *, task: Task, store: InMemoryTaskStore, depth: int) -> None: ...


# ──────────────────────────────────────────────────────────────────────────
# Method handlers.  Each one is a thin orchestration over `store`/`runner`.
# Bindings just call the relevant one and translate the result/raise to the
# appropriate wire format.
# ──────────────────────────────────────────────────────────────────────────


async def submit_message(
    *,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    message: Message,
    context_id: str | None = None,
    return_immediately: bool = False,
    depth: int = 1,
) -> Task:
    """``message:send`` (Section 9.4.1, 11.3.1).

    If ``return_immediately`` is True the task is created in ``SUBMITTED``
    state and returned right away — the runner finishes in the background.
    Otherwise we wait until the runner returns control (terminal or
    interrupted state).
    """
    task = await store.create(context_id=context_id, message=message)

    async def _run_in_bg():
        try:
            await runner.run_task(task=task, store=store, depth=depth)
        except A2AError as exc:
            await store.record_status(
                task.id,
                TaskState.FAILED,
                error=exc.to_jsonrpc_error(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("runner crashed for task %s", task.id)
            await store.record_status(
                task.id,
                TaskState.FAILED,
                error={"code": -32603, "message": str(exc)},
            )

    if return_immediately:
        asyncio.create_task(_run_in_bg())
        return await store.get(task.id)

    await _run_in_bg()
    return await store.get(task.id)


async def stream_message(
    *,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    message: Message,
    context_id: str | None = None,
    depth: int = 1,
):
    """``message:stream`` (Section 9.4.2, 11.3.2).

    Returns an async iterator that yields the first event (``Task``
    snapshot) followed by every status/artifact update until the task
    reaches a terminal state.
    """
    task = await store.create(context_id=context_id, message=message)
    sub = await store.subscribe(task.id)

    async def _drive():
        try:
            await runner.run_task(task=task, store=store, depth=depth)
        except A2AError as exc:
            await store.record_status(
                task.id,
                TaskState.FAILED,
                error=exc.to_jsonrpc_error(),
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("runner crashed for task %s", task.id)
            await store.record_status(
                task.id,
                TaskState.FAILED,
                error={"code": -32603, "message": str(exc)},
            )

    asyncio.create_task(_drive())

    async def _iter():
        yield {"task": task.model_dump(by_alias=True, exclude_none=True)}
        async for evt in sub:
            kind = "statusUpdate" if evt.__class__.__name__ == "TaskStatusUpdateEvent" else "artifactUpdate"
            yield {kind: evt.model_dump(by_alias=True, exclude_none=True)}

    return _iter()


async def get_task(
    *,
    store: InMemoryTaskStore,
    task_id: str,
    history_length: int | None = None,
) -> Task:
    """``tasks/get`` (Section 9.4.3, 11.3.3)."""
    return await store.get(task_id, history_length=history_length)


async def list_tasks(
    *,
    store: InMemoryTaskStore,
    context_id: str | None = None,
    status: str | None = None,
    page_size: int = 50,
    page_token: str = "",
    status_timestamp_after: float | None = None,
    include_artifacts: bool = True,
) -> dict[str, Any]:
    """``tasks/list`` (Section 11.3 ``GET /a2a/v1/tasks``)."""
    state_filter = None
    if status:
        try:
            state_filter = TaskState(status)
        except ValueError as exc:
            raise A2AError("InvalidParamsError", f"unknown task status: {status}") from exc

    items, next_token = await store.list(
        context_id=context_id,
        status=state_filter,
        page_size=page_size,
        page_token=page_token,
        status_timestamp_after=status_timestamp_after,
        include_artifacts=include_artifacts,
    )
    return {
        "tasks": [t.model_dump(by_alias=True, exclude_none=True) for t in items],
        "nextPageToken": next_token or "",
    }


async def cancel_task(*, store: InMemoryTaskStore, task_id: str) -> Task:
    """``tasks/cancel`` (Section 9.4.4, 11.3.4)."""
    return await store.cancel(task_id)


async def subscribe_task(*, store: InMemoryTaskStore, task_id: str):
    """``tasks/resubscribe`` (Section 9.4.5, 11.3.5).

    Replays the current task snapshot, then streams any subsequent events
    until the task terminates.
    """
    task = await store.get(task_id)
    sub = await store.subscribe(task_id)

    async def _iter():
        yield {"task": task.model_dump(by_alias=True, exclude_none=True)}
        async for evt in sub:
            kind = "statusUpdate" if evt.__class__.__name__ == "TaskStatusUpdateEvent" else "artifactUpdate"
            yield {kind: evt.model_dump(by_alias=True, exclude_none=True)}

    return _iter()


# ── Push-notification config CRUD (Section 7.5) ─────────────────────────────
async def set_push_config(
    *,
    store: InMemoryTaskStore,
    task_id: str,
    cfg: PushNotificationConfig,
) -> PushNotificationConfig:
    return await store.upsert_push_config(task_id, cfg)


async def list_push_configs(
    *,
    store: InMemoryTaskStore,
    task_id: str,
) -> list[PushNotificationConfig]:
    return await store.list_push_configs(task_id)


async def delete_push_config(
    *,
    store: InMemoryTaskStore,
    task_id: str,
    cfg_id: str,
) -> None:
    await store.delete_push_config(task_id, cfg_id)


__all__ = [
    "RunnerLike",
    "submit_message",
    "stream_message",
    "get_task",
    "list_tasks",
    "cancel_task",
    "subscribe_task",
    "set_push_config",
    "list_push_configs",
    "delete_push_config",
]
