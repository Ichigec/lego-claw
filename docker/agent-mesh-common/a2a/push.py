"""A2A push notification dispatcher (Section 7.5 of the spec).

When a client wants to be notified about a Task asynchronously — instead
of holding an SSE stream open — it registers a webhook through
``POST /a2a/v1/tasks/{taskId}/pushNotificationConfigs`` (Section 11.7).
The server is then responsible for delivering every subsequent status /
artifact update to that URL.

This module implements the delivery side:

* :class:`PushDispatcher` watches the configured task store and, whenever
  a webhook config is added (or already exists), spawns a background
  asyncio task per ``(task, config)`` pair that consumes the same event
  stream as SSE subscribers.
* Each outbound POST carries:

  - ``Content-Type: application/json``
  - ``X-A2A-Notification-Token: <cfg.token>`` (echoed verbatim per
    Section 7.5.1 so the receiver can correlate webhook → subscription
    without trusting the URL alone).
  - The optional ``Authorization`` header generated from
    :class:`PushNotificationAuthentication` (``bearer`` / ``apiKey`` /
    raw header pass-through).

* Delivery uses exponential backoff capped at ``max_retries`` attempts
  (defaults: ``5`` retries with ``base = 1.0 s`` → 1, 2, 4, 8, 16 s).
  After exhaustion the failed event is logged and dropped — A2A defines
  no spec-level retry semantics beyond best-effort, and clients can
  always catch up via ``POST /tasks/{id}:subscribe`` or
  ``GET /tasks/{id}``.

* Validation: the receiver of a webhook MUST verify
  :func:`validate_notification_token`. We expose that helper for adapters
  (or downstream services) to plug into their own webhook endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Any

from .task_store import TaskStore
from .types import (
    Artifact,
    Message,
    PushNotificationAuthentication,
    PushNotificationAuthenticationInfo,
    PushNotificationConfig,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

logger = logging.getLogger("a2a.push")

# Header name from Section 7.5.1.
NOTIFICATION_TOKEN_HEADER = "X-A2A-Notification-Token"


# ── Token helpers ───────────────────────────────────────────────────────────
def mint_notification_token(n_bytes: int = 32) -> str:
    """Return a URL-safe random token suitable for ``cfg.token``."""
    return secrets.token_urlsafe(n_bytes)


def validate_notification_token(received: str | None, expected: str | None) -> bool:
    """Constant-time compare for :data:`NOTIFICATION_TOKEN_HEADER`.

    If the server did not set a token on the original config (``expected``
    is ``None``), webhooks SHOULD still be accepted (Section 7.5.1) — the
    server explicitly opted out of correlation.  Otherwise the values
    must match exactly.
    """
    if expected is None:
        return True
    if not received:
        return False
    return secrets.compare_digest(str(received), str(expected))


# ── Delivery worker ────────────────────────────────────────────────────────
def _try_import_httpx():
    try:
        import httpx  # type: ignore

        return httpx
    except ImportError:
        logger.warning(
            "httpx not installed; push notification webhooks are disabled"
        )
        return None


def _auth_headers(cfg: PushNotificationConfig) -> dict[str, str]:
    """Translate an optional :class:`PushNotificationAuthentication` into
    HTTP headers.

    Supports two shapes:

    * the simple ``schemes + credentials`` form (used by the original
      P0 skeleton);
    * the typed ``PushNotificationAuthenticationInfo`` discriminated
      union (Section 4.3.x).
    """
    auth = cfg.authentication
    if auth is None:
        return {}
    if isinstance(auth, PushNotificationAuthentication):
        creds = (auth.credentials or "").strip()
        if not creds:
            return {}
        scheme = (auth.schemes[0] if auth.schemes else "bearer").lower()
        if scheme == "bearer":
            return {"Authorization": f"Bearer {creds}"}
        if scheme in {"basic", "apikey", "api-key"}:
            return {"Authorization": f"{auth.schemes[0]} {creds}"}
        return {"Authorization": creds}
    # PushNotificationAuthenticationInfo (discriminated union).
    info_type = getattr(auth, "type", None)
    creds = getattr(auth, "credentials", None) or ""
    if info_type == "apiKey":
        # Place credentials into the spec-declared header/query — we only
        # honour the header form for simplicity; query/cookie are noted
        # as a TODO.
        name = getattr(auth, "name", "Authorization")
        if creds:
            return {name: creds}
        return {}
    if info_type == "http":
        scheme = getattr(auth, "scheme", "bearer")
        if creds:
            return {"Authorization": f"{scheme} {creds}"}
        return {}
    return {}


class PushDispatcher:
    """Background webhook fan-out for an :class:`InMemoryTaskStore`.

    Lifecycle (typically wired from the FastAPI lifespan):

    .. code-block:: python

        dispatcher = PushDispatcher(store=task_store)
        await dispatcher.start()
        # ... serve traffic ...
        await dispatcher.stop()

    The dispatcher snapshots existing push configs at startup and then
    polls the store at ``poll_interval_s`` for newly-registered configs.
    Per (task, config) it spawns a delivery coroutine that pumps the
    SSE-style event stream and POSTs every event with retry/backoff.
    """

    def __init__(
        self,
        *,
        store: TaskStore,
        max_retries: int | None = None,
        backoff_base_s: float | None = None,
        backoff_cap_s: float = 30.0,
        timeout_s: float = 10.0,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.store = store
        self.max_retries = (
            max_retries
            if max_retries is not None
            else int(os.environ.get("A2A_PUSH_MAX_RETRIES", "5"))
        )
        self.backoff_base_s = (
            backoff_base_s
            if backoff_base_s is not None
            else float(os.environ.get("A2A_PUSH_BACKOFF_BASE_S", "1.0"))
        )
        self.backoff_cap_s = backoff_cap_s
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s

        self._tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._poll_task: asyncio.Task[Any] | None = None
        self._stopped = asyncio.Event()
        self._httpx = _try_import_httpx()

    # ── Public lifecycle ─────────────────────────────────────────────────
    async def start(self) -> None:
        if self._poll_task is not None:
            return
        if self._httpx is None:
            return
        self._stopped.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="a2a-push-poll")
        logger.info(
            "push dispatcher started; max_retries=%d backoff_base_s=%.2f poll=%.1fs",
            self.max_retries,
            self.backoff_base_s,
            self.poll_interval_s,
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._poll_task = None
        # Cancel every per-config delivery task.
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # ── Hook for explicit registration (rest.py can call this immediately
    # after `add_push_config` so the dispatcher attaches without waiting
    # for the next poll tick).
    def register(self, task_id: str, cfg: PushNotificationConfig) -> None:
        if self._httpx is None:
            return
        key = (task_id, cfg.id)
        if key in self._tasks and not self._tasks[key].done():
            return
        self._tasks[key] = asyncio.create_task(
            self._deliver(task_id, cfg), name=f"a2a-push:{task_id[:8]}/{cfg.id[:8]}"
        )

    # ── Internals ────────────────────────────────────────────────────────
    async def _poll_loop(self) -> None:
        """Sweep the task store and attach delivery workers for new configs."""
        try:
            while not self._stopped.is_set():
                try:
                    await self._sweep()
                except Exception:  # noqa: BLE001
                    logger.exception("push dispatcher sweep crashed")
                try:
                    await asyncio.wait_for(self._stopped.wait(), self.poll_interval_s)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            return

    async def _sweep(self) -> None:
        # The store API does not enumerate tasks for us in a single call;
        # use the `list_tasks` shim from InMemoryTaskStore.  Iterate over
        # all known tasks and pick up configs we have not attached to yet.
        if not hasattr(self.store, "list_tasks"):
            return
        try:
            from .task_store import ListFilter

            page = await self.store.list_tasks(  # type: ignore[attr-defined]
                ListFilter(page_size=500, include_artifacts=False, history_length=0)
            )
        except Exception:  # noqa: BLE001
            return
        for task in page.tasks:
            try:
                cfgs = await self.store.list_push_configs(task.id)
            except Exception:  # noqa: BLE001
                continue
            for cfg in cfgs:
                self.register(task.id, cfg)

    async def _deliver(self, task_id: str, cfg: PushNotificationConfig) -> None:
        """Pump the task event stream and POST each event to ``cfg.url``."""
        if self._httpx is None:
            return
        # Emit the current snapshot first so a late-attached webhook sees
        # the same first event SSE subscribers would have seen.
        try:
            snapshot = await self.store.get_task(task_id)
            await self._post_event(cfg, _snapshot_payload(snapshot))
        except Exception:  # noqa: BLE001
            logger.warning("push: failed to deliver initial snapshot for %s", task_id)
            return

        try:
            async for evt in self.store.subscribe(task_id):
                if self._stopped.is_set():
                    return
                payload = _event_payload(evt)
                if payload is None:
                    continue
                await self._post_event(cfg, payload)
                if _is_terminal_event(evt):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("push: subscriber for %s crashed", task_id)

    async def _post_event(
        self,
        cfg: PushNotificationConfig,
        payload: dict[str, Any],
    ) -> None:
        if self._httpx is None:
            return
        headers = {"Content-Type": "application/json"}
        headers.update(_auth_headers(cfg))
        if cfg.token:
            headers[NOTIFICATION_TOKEN_HEADER] = cfg.token

        attempt = 0
        while attempt <= self.max_retries and not self._stopped.is_set():
            try:
                async with self._httpx.AsyncClient(timeout=self.timeout_s) as client:
                    resp = await client.post(cfg.url, json=payload, headers=headers)
                    if 200 <= resp.status_code < 300:
                        if attempt:
                            logger.info(
                                "push: delivered to %s after %d retries", cfg.url, attempt
                            )
                        return
                    logger.warning(
                        "push: %s returned HTTP %d (attempt %d/%d)",
                        cfg.url,
                        resp.status_code,
                        attempt + 1,
                        self.max_retries + 1,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "push: POST %s failed (attempt %d/%d): %s",
                    cfg.url,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
            if attempt == self.max_retries:
                break
            backoff = min(self.backoff_base_s * (2 ** attempt), self.backoff_cap_s)
            try:
                await asyncio.wait_for(self._stopped.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            attempt += 1
        logger.error(
            "push: giving up after %d attempts; dropping event for %s",
            self.max_retries + 1,
            cfg.url,
        )


# ── Event payload normalisation ─────────────────────────────────────────────
def _snapshot_payload(task: Task) -> dict[str, Any]:
    return {
        "kind": "task",
        "data": task.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def _event_payload(evt: Any) -> dict[str, Any] | None:
    if isinstance(evt, Task):
        return _snapshot_payload(evt)
    if isinstance(evt, Message):
        return {
            "kind": "message",
            "data": evt.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    if isinstance(evt, TaskStatusUpdateEvent):
        return {
            "kind": "status-update",
            "data": evt.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    if isinstance(evt, TaskArtifactUpdateEvent):
        return {
            "kind": "artifact-update",
            "data": evt.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    # Unknown event — best-effort serialise.
    if hasattr(evt, "model_dump"):
        return {"kind": "unknown", "data": evt.model_dump(mode="json", by_alias=True, exclude_none=True)}
    return None


def _is_terminal_event(evt: Any) -> bool:
    if isinstance(evt, TaskStatusUpdateEvent) and evt.final:
        return True
    if isinstance(evt, Task) and getattr(evt.status.state, "is_terminal", False):
        return True
    return False


# ── Glue helper used by rest.py to wire register() into add_push_config ────
def attach_to_router(
    *,
    dispatcher: PushDispatcher,
    add_push_config_orig,
):
    """Decorate the existing ``add_push_config`` handler so the dispatcher
    learns about new configs immediately (without waiting for the next
    poll tick).
    """

    async def _wrapped(*args, **kwargs):
        result = await add_push_config_orig(*args, **kwargs)
        # The original handler receives `task_id` and a `PushNotificationConfig`;
        # rebuild from kwargs if available, else best-effort from the returned
        # JSON-able result.
        task_id = kwargs.get("task_id") or (args[0] if args else None)
        cfg = kwargs.get("config")
        if isinstance(cfg, PushNotificationConfig) and task_id:
            dispatcher.register(task_id, cfg)
        return result

    return _wrapped


__all__ = [
    "PushDispatcher",
    "mint_notification_token",
    "validate_notification_token",
    "NOTIFICATION_TOKEN_HEADER",
    "attach_to_router",
]
