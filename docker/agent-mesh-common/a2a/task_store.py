"""Task storage backends for the A2A REST/JSON-RPC/gRPC bindings.

The :class:`TaskStore` ABC pins down the surface that
:mod:`agent_mesh_adapter.a2a.rest` (and the upcoming JSON-RPC / gRPC
servers) consume. Two concrete implementations are shipped:

* :class:`InMemoryTaskStore` — default, used by P0 of the plan. Cheap
  but loses state on restart.
* :class:`PostgresTaskStore` — P2, sketches the persistent backend
  (``A2A_TASK_STORE_DSN=postgresql://…``). Implementation is left as a
  stub with a NotImplementedError because the surrounding wiring (DB
  migrations, connection pool) is delivered later.

Both stores implement cursor-based pagination as defined by Section
3.1.4 of the A2A spec — opaque ``pageToken`` derived from
(``statusTimestamp``, ``id``) so iteration is stable under concurrent
inserts and the caller never sees a duplicated row.

Filtering supported on ``ListTasks`` (Section 11.5):

* ``context_id`` — exact match.
* ``state`` — set of :class:`TaskState` values; ``None`` = any.
* ``status_timestamp_after`` — inclusive RFC-3339 lower bound on
  ``status.timestamp`` (after a successful update the timestamp is
  bumped, so this gives the canonical "what changed since X" cursor).

Per Section 3.1.4 the response can optionally trim ``history`` and
``artifacts`` via ``history_length`` / ``include_artifacts`` so the
filter knobs are part of the store contract rather than the REST layer.

Backwards-compat surface
========================
The earlier P0 skeleton exposed a smaller method set
(:meth:`create`, :meth:`get`, :meth:`list`, :meth:`cancel`,
:meth:`upsert_push_config`, :meth:`list_push_configs`,
:meth:`delete_push_config`, plus ``error=`` and old-style return values
on :meth:`record_status`/:meth:`append_artifact`). We keep those as
thin shims so the existing ``handlers.py`` keeps working unchanged.
"""
from __future__ import annotations

import abc
import asyncio
import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .types import (
    A2AError,
    Artifact,
    Message,
    PushNotificationConfig,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    utc_now_iso,
)

logger = logging.getLogger("a2a.task_store")


# ── public exceptions (mapped to RFC 7807 problems by rest.py) ──────────────
class TaskStoreError(Exception):
    """Base class for store-level failures."""


class TaskNotFoundError(TaskStoreError):
    pass


class PushConfigNotFoundError(TaskStoreError):
    pass


class TaskNotCancelableError(TaskStoreError):
    pass


# ── pagination cursor (Section 3.1.4) ────────────────────────────────────────
def _encode_page_token(timestamp: str, task_id: str) -> str:
    raw = json.dumps({"t": timestamp, "i": task_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_page_token(token: str | None) -> tuple[str, str] | None:
    if not token:
        return None
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
        obj = json.loads(raw)
        return str(obj["t"]), str(obj["i"])
    except Exception:  # noqa: BLE001 — opaque token, surface as "no cursor"
        logger.warning("ignoring malformed pageToken=%r", token)
        return None


@dataclass
class TaskListPage:
    tasks: list[Task]
    next_page_token: str | None = None


@dataclass
class ListFilter:
    """Filter for :meth:`TaskStore.list_tasks`."""

    context_id: str | None = None
    states: tuple[TaskState, ...] | None = None
    status_timestamp_after: str | None = None
    page_size: int = 50
    page_token: str | None = None
    history_length: int | None = None
    include_artifacts: bool = True

    def trim(self, task: Task) -> Task:
        """Apply ``historyLength``/``includeArtifacts`` projection."""
        history = task.history
        if self.history_length is not None and self.history_length >= 0:
            history = history[-self.history_length :] if self.history_length else []
        artifacts = task.artifacts if self.include_artifacts else []
        if history is task.history and artifacts is task.artifacts:
            return task
        return task.model_copy(update={"history": history, "artifacts": artifacts})


# ── ABC ─────────────────────────────────────────────────────────────────────
class TaskStore(abc.ABC):
    """Abstract task store.

    All methods are coroutines so a future Postgres / Redis backend can
    wire in async drivers without changing the call sites.
    """

    @abc.abstractmethod
    async def create_task(
        self,
        *,
        context_id: str | None,
        initial_message: Message | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task: ...

    @abc.abstractmethod
    async def get_task(
        self,
        task_id: str,
        *,
        history_length: int | None = None,
        include_artifacts: bool = True,
    ) -> Task: ...

    @abc.abstractmethod
    async def list_tasks(self, filter_: ListFilter) -> TaskListPage: ...

    @abc.abstractmethod
    async def append_message(self, task_id: str, message: Message) -> Task: ...

    @abc.abstractmethod
    async def append_artifact(
        self,
        task_id: str,
        artifact: Artifact,
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> Task: ...

    @abc.abstractmethod
    async def record_status(
        self,
        task_id: str,
        state: TaskState,
        *,
        message: Message | None = None,
        final: bool | None = None,
    ) -> Task: ...

    @abc.abstractmethod
    async def cancel_task(self, task_id: str) -> Task: ...

    # ── streaming subscription ───────────────────────────────────────────
    @abc.abstractmethod
    def subscribe(self, task_id: str) -> AsyncIterator[Any]:
        """Async iterator yielding ``Task | TaskStatusUpdateEvent |
        TaskArtifactUpdateEvent | Message`` events for a single task.

        Implementations MUST emit the current ``Task`` snapshot first,
        then yield each subsequent event until a terminal status arrives,
        after which the iterator must stop.
        """

    # ── push notification configs (Section 4.3) ──────────────────────────
    @abc.abstractmethod
    async def add_push_config(
        self, task_id: str, config: PushNotificationConfig
    ) -> PushNotificationConfig: ...

    @abc.abstractmethod
    async def list_push_configs(
        self, task_id: str
    ) -> list[PushNotificationConfig]: ...

    @abc.abstractmethod
    async def get_push_config(
        self, task_id: str, config_id: str
    ) -> PushNotificationConfig: ...

    @abc.abstractmethod
    async def delete_push_config(self, task_id: str, config_id: str) -> None: ...


# ── In-memory implementation ────────────────────────────────────────────────
@dataclass
class _Subscription:
    queue: asyncio.Queue[Any]
    closed: bool = False


@dataclass
class _TaskRecord:
    task: Task
    push_configs: dict[str, PushNotificationConfig] = field(default_factory=dict)
    subscribers: list[_Subscription] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_SUBSCRIPTION_CLOSED = object()


class InMemoryTaskStore(TaskStore):
    """Default in-memory backend.

    Trade-offs:

    * Process-local — fine for the single-replica adapters that we ship
      in the compose file. Replace with :class:`PostgresTaskStore` when
      moving to multi-replica deployments.
    * O(n) ``list_tasks`` — acceptable while a single agent stays under
      ~10 k tasks; switch to indexed Postgres queries beyond that.
    """

    def __init__(self, *, max_tasks: int = 10_000) -> None:
        self._tasks: dict[str, _TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._max_tasks = max_tasks

    @staticmethod
    def _new_id() -> str:
        # Section 4.1.1: ids must be opaque, unique, ≤ 128 chars.
        # We use a 32-char hex (UUID4 without dashes) for legibility.
        return uuid.uuid4().hex

    async def create_task(
        self,
        *,
        context_id: str | None,
        initial_message: Message | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        async with self._lock:
            if len(self._tasks) >= self._max_tasks:
                # Evict oldest by status.timestamp — keeps the API
                # responsive instead of OOM-ing the adapter.
                oldest = min(
                    self._tasks.values(),
                    key=lambda r: r.task.status.timestamp,
                )
                self._tasks.pop(oldest.task.id, None)
            task_id = self._new_id()
            ctx = context_id or self._new_id()
            history: list[Message] = []
            if initial_message is not None:
                msg = initial_message.model_copy(
                    update={"task_id": task_id, "context_id": ctx}
                )
                history.append(msg)
            task = Task(
                id=task_id,
                context_id=ctx,
                status=TaskStatus(state=TaskState.SUBMITTED),
                history=history,
                artifacts=[],
                metadata=metadata,
            )
            self._tasks[task_id] = _TaskRecord(task=task)
            return task

    async def _record(self, task_id: str) -> _TaskRecord:
        rec = self._tasks.get(task_id)
        if rec is None:
            raise TaskNotFoundError(task_id)
        return rec

    async def get_task(
        self,
        task_id: str,
        *,
        history_length: int | None = None,
        include_artifacts: bool = True,
    ) -> Task:
        rec = await self._record(task_id)
        task = rec.task
        return ListFilter(
            history_length=history_length,
            include_artifacts=include_artifacts,
        ).trim(task)

    async def list_tasks(self, filter_: ListFilter) -> TaskListPage:
        async with self._lock:
            records = list(self._tasks.values())

        records.sort(key=lambda r: (r.task.status.timestamp, r.task.id))

        cursor = _decode_page_token(filter_.page_token)

        def _state_value(s: Any) -> Any:
            return s.value if hasattr(s, "value") else s

        def _matches(rec: _TaskRecord) -> bool:
            t = rec.task
            if filter_.context_id and t.context_id != filter_.context_id:
                return False
            if filter_.states is not None:
                wanted = {_state_value(s) for s in filter_.states}
                if _state_value(t.status.state) not in wanted:
                    return False
            if (
                filter_.status_timestamp_after
                and t.status.timestamp < filter_.status_timestamp_after
            ):
                return False
            if cursor is not None:
                key = (t.status.timestamp, t.id)
                if key <= cursor:
                    return False
            return True

        page_size = max(1, min(filter_.page_size, 500))
        matched: list[Task] = []
        last_key: tuple[str, str] | None = None
        for rec in records:
            if not _matches(rec):
                continue
            matched.append(filter_.trim(rec.task))
            last_key = (rec.task.status.timestamp, rec.task.id)
            if len(matched) >= page_size:
                break

        next_token: str | None = None
        if last_key is not None and len(matched) >= page_size:
            for rec in records:
                if (rec.task.status.timestamp, rec.task.id) <= last_key:
                    continue
                t = rec.task
                if (
                    filter_.context_id
                    and t.context_id != filter_.context_id
                ):
                    continue
                if filter_.states is not None:
                    wanted = {_state_value(s) for s in filter_.states}
                    if _state_value(t.status.state) not in wanted:
                        continue
                if (
                    filter_.status_timestamp_after
                    and t.status.timestamp < filter_.status_timestamp_after
                ):
                    continue
                next_token = _encode_page_token(last_key[0], last_key[1])
                break
        return TaskListPage(tasks=matched, next_page_token=next_token)

    async def append_message(self, task_id: str, message: Message) -> Task:
        rec = await self._record(task_id)
        async with rec.lock:
            ctx = rec.task.context_id
            msg = message.model_copy(update={"task_id": task_id, "context_id": ctx})
            rec.task = rec.task.model_copy(
                update={"history": [*rec.task.history, msg]}
            )
            await self._broadcast(rec, msg)
            return rec.task

    async def append_artifact(
        self,
        task_id: str,
        artifact: Artifact,
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> Task:
        rec = await self._record(task_id)
        async with rec.lock:
            artifacts = list(rec.task.artifacts)
            existing_ix = next(
                (
                    i
                    for i, a in enumerate(artifacts)
                    if a.artifact_id == artifact.artifact_id
                ),
                -1,
            )
            if existing_ix >= 0 and append:
                merged = artifacts[existing_ix].model_copy(
                    update={
                        "parts": [*artifacts[existing_ix].parts, *artifact.parts],
                    }
                )
                artifacts[existing_ix] = merged
            elif existing_ix >= 0:
                artifacts[existing_ix] = artifact
            else:
                artifacts.append(artifact)
            rec.task = rec.task.model_copy(update={"artifacts": artifacts})
            event = TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=rec.task.context_id,
                artifact=artifact,
                append=append,
                last_chunk=last_chunk,
            )
            await self._broadcast(rec, event)
            return rec.task

    async def record_status(
        self,
        task_id: str,
        state: TaskState,
        *,
        message: Message | None = None,
        final: bool | None = None,
        error: dict[str, Any] | None = None,
    ) -> Task:
        rec = await self._record(task_id)
        async with rec.lock:
            new_status = TaskStatus(
                state=state,
                message=message,
                timestamp=utc_now_iso(),
                error=error,
            )
            rec.task = rec.task.model_copy(update={"status": new_status})
            terminal = state.is_terminal
            is_final = terminal if final is None else final
            event = TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=rec.task.context_id,
                status=new_status,
                final=is_final,
            )
            await self._broadcast(rec, event)
            if terminal:
                await self._close_subscribers(rec)
            return rec.task

    async def cancel_task(self, task_id: str) -> Task:
        rec = await self._record(task_id)
        if rec.task.status.state.is_terminal:
            raise TaskNotCancelableError(task_id)
        return await self.record_status(task_id, TaskState.CANCELED, final=True)

    # ── streaming subscription ───────────────────────────────────────────
    async def subscribe(  # type: ignore[override]
        self, task_id: str
    ) -> AsyncIterator[Any]:
        rec = await self._record(task_id)
        sub = _Subscription(queue=asyncio.Queue(maxsize=512))
        async with rec.lock:
            snapshot = rec.task
            rec.subscribers.append(sub)
            already_terminal = rec.task.status.state.is_terminal
        try:
            yield snapshot
            if already_terminal:
                return
            while True:
                event = await sub.queue.get()
                if event is _SUBSCRIPTION_CLOSED:
                    return
                yield event
                if (
                    isinstance(event, TaskStatusUpdateEvent)
                    and event.final
                ):
                    return
        finally:
            async with rec.lock:
                if sub in rec.subscribers:
                    rec.subscribers.remove(sub)
            sub.closed = True

    async def _broadcast(self, rec: _TaskRecord, event: Any) -> None:
        for sub in list(rec.subscribers):
            if sub.closed:
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "subscriber queue full; dropping oldest event "
                    "(task=%s)",
                    rec.task.id,
                )
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    sub.closed = True

    async def _close_subscribers(self, rec: _TaskRecord) -> None:
        for sub in list(rec.subscribers):
            try:
                sub.queue.put_nowait(_SUBSCRIPTION_CLOSED)
            except asyncio.QueueFull:
                sub.closed = True

    # ── push notification configs ───────────────────────────────────────
    async def add_push_config(
        self, task_id: str, config: PushNotificationConfig
    ) -> PushNotificationConfig:
        rec = await self._record(task_id)
        async with rec.lock:
            if not config.id:
                config = config.model_copy(update={"id": self._new_id()})
            rec.push_configs[config.id] = config
            return config

    async def list_push_configs(
        self, task_id: str
    ) -> list[PushNotificationConfig]:
        rec = await self._record(task_id)
        return list(rec.push_configs.values())

    async def get_push_config(
        self, task_id: str, config_id: str
    ) -> PushNotificationConfig:
        rec = await self._record(task_id)
        cfg = rec.push_configs.get(config_id)
        if cfg is None:
            raise PushConfigNotFoundError(f"{task_id}/{config_id}")
        return cfg

    async def delete_push_config(self, task_id: str, config_id: str) -> None:
        rec = await self._record(task_id)
        if config_id not in rec.push_configs:
            raise PushConfigNotFoundError(f"{task_id}/{config_id}")
        async with rec.lock:
            rec.push_configs.pop(config_id, None)

    # ── backwards-compat shims (used by handlers.py) ─────────────────────
    async def create(
        self,
        *,
        context_id: str | None = None,
        message: Message | None = None,
    ) -> Task:
        return await self.create_task(
            context_id=context_id, initial_message=message
        )

    async def get(self, task_id: str, *, history_length: int | None = None) -> Task:
        try:
            return await self.get_task(task_id, history_length=history_length)
        except TaskNotFoundError as exc:
            raise A2AError("TaskNotFoundError", f"Task {task_id} not found") from exc

    async def list(
        self,
        *,
        context_id: str | None = None,
        status: TaskState | None = None,
        page_size: int = 50,
        page_token: str = "",
        status_timestamp_after: float | str | None = None,
        include_artifacts: bool = True,
    ) -> tuple[list[Task], str | None]:
        states = (status,) if status is not None else None
        ts_after: str | None
        if isinstance(status_timestamp_after, (int, float)):
            from datetime import datetime, timezone

            ts_after = (
                datetime.fromtimestamp(float(status_timestamp_after), tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        else:
            ts_after = status_timestamp_after
        page = await self.list_tasks(
            ListFilter(
                context_id=context_id,
                states=states,
                page_size=page_size,
                page_token=page_token or None,
                status_timestamp_after=ts_after,
                include_artifacts=include_artifacts,
            )
        )
        return page.tasks, page.next_page_token

    async def cancel(self, task_id: str) -> Task:
        try:
            return await self.cancel_task(task_id)
        except TaskNotFoundError as exc:
            raise A2AError("TaskNotFoundError", f"Task {task_id} not found") from exc
        except TaskNotCancelableError as exc:
            raise A2AError(
                "TaskNotCancelableError", f"Task {task_id} cannot be cancelled"
            ) from exc

    async def upsert_push_config(
        self, task_id: str, cfg: PushNotificationConfig
    ) -> PushNotificationConfig:
        try:
            return await self.add_push_config(task_id, cfg)
        except TaskNotFoundError as exc:
            raise A2AError("TaskNotFoundError", f"Task {task_id} not found") from exc


# ── Postgres backend (P2 stub) ───────────────────────────────────────────────
class PostgresTaskStore(TaskStore):
    """Persistent backend (P2 of the plan).

    The schema mirrors the JSON wire format: a single ``a2a_tasks`` row
    per task with the canonical Task JSON, plus ``a2a_push_configs``
    keyed by ``(task_id, config_id)``. ``ListTasks`` uses an index on
    ``(status_timestamp, id)`` to stream cursor pages.

    Implementation is intentionally deferred (P2 in section 6 of the
    plan). Construction raises so the wiring code can fail loudly when
    someone forgets to enable Postgres deliverable.
    """

    def __init__(self, dsn: str) -> None:  # pragma: no cover - stub
        self.dsn = dsn
        raise NotImplementedError(
            "PostgresTaskStore is a P2 deliverable; use InMemoryTaskStore "
            "until docker/agent-mesh-common/a2a/postgres.py lands."
        )

    async def create_task(self, **kwargs: Any) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def get_task(self, *args: Any, **kwargs: Any) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def list_tasks(
        self, filter_: ListFilter
    ) -> TaskListPage:  # pragma: no cover
        raise NotImplementedError

    async def append_message(
        self, task_id: str, message: Message
    ) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def append_artifact(
        self,
        task_id: str,
        artifact: Artifact,
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def record_status(
        self,
        task_id: str,
        state: TaskState,
        *,
        message: Message | None = None,
        final: bool | None = None,
    ) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def cancel_task(self, task_id: str) -> Task:  # pragma: no cover
        raise NotImplementedError

    async def subscribe(  # type: ignore[override]
        self, task_id: str
    ) -> AsyncIterator[Any]:  # pragma: no cover
        raise NotImplementedError
        yield  # pragma: no cover — marker so this is treated as async-gen

    async def add_push_config(
        self, task_id: str, config: PushNotificationConfig
    ) -> PushNotificationConfig:  # pragma: no cover
        raise NotImplementedError

    async def list_push_configs(
        self, task_id: str
    ) -> list[PushNotificationConfig]:  # pragma: no cover
        raise NotImplementedError

    async def get_push_config(
        self, task_id: str, config_id: str
    ) -> PushNotificationConfig:  # pragma: no cover
        raise NotImplementedError

    async def delete_push_config(
        self, task_id: str, config_id: str
    ) -> None:  # pragma: no cover
        raise NotImplementedError


# ── factory ─────────────────────────────────────────────────────────────────
def build_task_store(dsn: str | None = None) -> TaskStore:
    """Pick a backend from the ``A2A_TASK_STORE_DSN`` env value."""
    dsn = (dsn or "memory://").strip()
    if not dsn or dsn.startswith("memory://"):
        return InMemoryTaskStore()
    if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
        return PostgresTaskStore(dsn)
    raise ValueError(
        f"unsupported A2A_TASK_STORE_DSN scheme: {dsn!r} "
        "(supported: 'memory://', 'postgresql://')"
    )


__all__ = [
    "ListFilter",
    "TaskListPage",
    "TaskStore",
    "InMemoryTaskStore",
    "PostgresTaskStore",
    "TaskStoreError",
    "TaskNotFoundError",
    "PushConfigNotFoundError",
    "TaskNotCancelableError",
    "build_task_store",
]
