"""In-task authorization helpers (A2A Section 7.6).

The A2A protocol lets a long-running task pause itself in
``TASK_STATE_AUTH_REQUIRED`` whenever the agent discovers that it needs
additional credentials it does not yet hold — for example, when a
remote API rejected the existing token, when MFA is required, or when
the user has not yet consented to a particular OAuth scope.

The handshake (per the spec):

1. Runner detects the auth gap and calls
   :func:`request_auth` with a structured ``AuthChallenge`` describing
   the requirement (schemes, scopes, optional ``credentialsUrl``).
2. Server transitions the task to ``TASK_STATE_AUTH_REQUIRED`` and emits
   the challenge as a ``message`` on the status update (visible to any
   SSE/JSON-RPC/gRPC subscriber).
3. Client retrieves the challenge through ``GET /tasks/{id}`` or by
   resubscribing; supplies the credential via the normal application
   path (out-of-band) **and** calls
   ``POST /tasks/{id}/auth:provide`` (a small adapter-level endpoint)
   with a one-shot envelope payload that this module decodes.
4. Server stores the supplied credentials on the in-task secret store,
   transitions the task back to ``TASK_STATE_WORKING``, and resumes the
   runner via :func:`await_auth` (the runner is awaiting the
   :class:`asyncio.Future` returned by :func:`request_auth`).

The whole flow is async-safe and survives reconnects: a client that
falls off the stream still sees ``TASK_STATE_AUTH_REQUIRED`` on the next
``GET /tasks/{id}`` and can resume from there.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .task_store import TaskNotFoundError, TaskStore
from .types import (
    A2AError,
    Message,
    Role,
    TaskState,
    TextPart,
)

logger = logging.getLogger("a2a.auth")


# ── Challenge / response models ─────────────────────────────────────────────
@dataclass
class AuthChallenge:
    """What the runner is asking the client for (Section 7.6.1)."""

    schemes: tuple[str, ...] = ("bearer",)
    """Authentication schemes the agent will accept, ordered by preference."""

    scopes: tuple[str, ...] = ()
    """Optional OAuth scopes / API permissions the agent needs."""

    description: str = ""
    """Human-readable rationale shown to the user via the
    ``TASK_STATE_AUTH_REQUIRED`` status message."""

    credentials_url: str | None = None
    """Optional OAuth/OIDC starting point the client can deep-link the
    user to. Mirrors the same field on
    :class:`a2a.types.OpenIDConnectSecurityScheme`."""

    challenge_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """Idempotency / correlation id; included in the status message and
    expected back from :func:`provide_credentials`."""

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "challengeId": self.challenge_id,
            "schemes": list(self.schemes),
            "scopes": list(self.scopes),
        }
        if self.description:
            out["description"] = self.description
        if self.credentials_url:
            out["credentialsUrl"] = self.credentials_url
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


@dataclass
class AuthCredentials:
    """What the client supplied (Section 7.6.2)."""

    scheme: str
    value: str
    """Opaque credential payload: bearer token, api-key, base64 of
    ``user:pass``, etc. — interpretation is up to the agent."""

    challenge_id: str | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Pending-challenge registry ──────────────────────────────────────────────
@dataclass
class _PendingChallenge:
    challenge: AuthChallenge
    future: asyncio.Future[AuthCredentials]
    created_at: float = field(default_factory=time.time)


class AuthChallengeRegistry:
    """Holds the in-flight ``TASK_STATE_AUTH_REQUIRED`` futures.

    The registry is intentionally separate from :class:`TaskStore` because
    it owns ``asyncio.Future`` objects (which are not serialisable) and
    its lifetime equals the FastAPI process — auth flows do not survive
    adapter restarts (clients must retry from scratch, see Section 7.6
    "Resilience").
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingChallenge] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, task_id: str, challenge: AuthChallenge
    ) -> asyncio.Future[AuthCredentials]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[AuthCredentials] = loop.create_future()
        async with self._lock:
            self._pending[task_id] = _PendingChallenge(challenge=challenge, future=fut)
        return fut

    async def resolve(
        self, task_id: str, credentials: AuthCredentials
    ) -> AuthChallenge:
        async with self._lock:
            pending = self._pending.pop(task_id, None)
        if pending is None:
            raise A2AError(
                "InvalidRequestError",
                f"No auth challenge pending for task {task_id}",
            )
        if (
            credentials.challenge_id
            and pending.challenge.challenge_id
            and credentials.challenge_id != pending.challenge.challenge_id
        ):
            # Re-register so the client can retry with the correct id; the
            # runner is still waiting on the original future.
            async with self._lock:
                self._pending[task_id] = pending
            raise A2AError(
                "InvalidRequestError",
                "challengeId mismatch — please re-fetch the task to get the latest challenge",
            )
        if not pending.future.done():
            pending.future.set_result(credentials)
        return pending.challenge

    async def get(self, task_id: str) -> AuthChallenge | None:
        async with self._lock:
            pending = self._pending.get(task_id)
        return pending.challenge if pending else None

    async def cancel(self, task_id: str, *, reason: str = "cancelled") -> None:
        async with self._lock:
            pending = self._pending.pop(task_id, None)
        if pending and not pending.future.done():
            pending.future.set_exception(
                A2AError("InvalidRequestError", f"auth challenge {reason}")
            )


# ── High-level helpers (runner-facing) ─────────────────────────────────────
async def request_auth(
    *,
    task_id: str,
    challenge: AuthChallenge,
    store: TaskStore,
    registry: AuthChallengeRegistry,
) -> AuthCredentials:
    """Runner-facing entry point.

    Atomically:

    1. registers the challenge so future ``provide_credentials`` calls
       can resolve it;
    2. transitions the task to ``TASK_STATE_AUTH_REQUIRED`` with a
       human-readable status message;
    3. awaits the client's credential reply (or a cancellation / failure).

    The returned :class:`AuthCredentials` is whatever the client posted.
    On cancellation (``tasks/cancel`` from the client) the awaiting
    coroutine raises :class:`A2AError`.
    """
    fut = await registry.register(task_id, challenge)
    status_message = _build_status_message(task_id, challenge)
    try:
        await store.record_status(
            task_id,
            TaskState.AUTH_REQUIRED,
            message=status_message,
            final=False,
        )
    except Exception as exc:
        await registry.cancel(task_id, reason=f"status write failed: {exc}")
        raise
    try:
        return await fut
    except asyncio.CancelledError:
        await registry.cancel(task_id, reason="task cancelled while waiting for auth")
        raise


async def await_auth(
    *,
    task_id: str,
    registry: AuthChallengeRegistry,
) -> AuthCredentials:
    """Convenience wrapper used from runners that already issued the
    ``record_status`` themselves and only need to wait.

    Returns the same credentials :func:`request_auth` would have returned.
    Raises :class:`A2AError` if no challenge is registered for this task.
    """
    async with registry._lock:  # noqa: SLF001 - internal use
        pending = registry._pending.get(task_id)  # noqa: SLF001
    if pending is None:
        raise A2AError(
            "InvalidRequestError",
            f"No auth challenge registered for task {task_id}; call request_auth first",
        )
    return await pending.future


async def provide_credentials(
    *,
    task_id: str,
    credentials: AuthCredentials,
    store: TaskStore,
    registry: AuthChallengeRegistry,
    resume_state: TaskState = TaskState.WORKING,
) -> None:
    """Client-facing entry point (called by the REST/JSON-RPC adapter).

    Resolves the pending challenge and transitions the task back to
    ``resume_state`` (typically ``WORKING``).
    """
    try:
        await store.get_task(task_id, history_length=0, include_artifacts=False)
    except TaskNotFoundError as exc:
        raise A2AError("TaskNotFoundError", f"Task {task_id} not found") from exc

    await registry.resolve(task_id, credentials)

    resume_message = Message(
        message_id=uuid.uuid4().hex,
        role=Role.AGENT,
        parts=[
            TextPart(text="Credentials accepted; resuming agent execution.")
        ],
    )
    await store.record_status(task_id, resume_state, message=resume_message, final=False)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _build_status_message(task_id: str, challenge: AuthChallenge) -> Message:
    desc = (
        f"Agent requires additional authentication ({', '.join(challenge.schemes)}). "
        f"{challenge.description or 'Provide credentials via tasks/auth:provide.'}"
    )
    return Message(
        message_id=uuid.uuid4().hex,
        role=Role.AGENT,
        parts=[TextPart(text=desc)],
        metadata={
            "authChallenge": challenge.to_payload(),
        },
    )


# ── FastAPI body model (module-scope so pydantic can build a TypeAdapter) ─
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


class AuthProvideBody(_CamelModel):
    """Request body of ``POST /tasks/{taskId}/auth:provide``."""

    scheme: str = "bearer"
    value: str = Field(..., min_length=1)
    challenge_id: str | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] | None = None


# ── FastAPI router glue (mountable on the same prefix as the REST binding) ─
def build_auth_router(
    *,
    store: TaskStore,
    registry: AuthChallengeRegistry,
    bearer_dep,
    prefix: str = "",
):
    """Tiny router exposing ``POST /tasks/{taskId}/auth:provide``.

    The route name is **not** part of the A2A spec (the spec leaves the
    in-task auth transport open) but mirrors the spec's URL grammar so
    callers can guess it.  Adapters may rewire to a different verb if
    they prefer ``message:send`` semantics with a sentinel metadata flag.
    """
    from fastapi import APIRouter, Body, Depends
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix=prefix)

    @router.post(
        "/tasks/{task_id}/auth:provide",
        dependencies=[Depends(bearer_dep)] if bearer_dep else [],
        summary="Provide credentials for a task waiting in TASK_STATE_AUTH_REQUIRED.",
    )
    async def auth_provide(task_id: str, body: AuthProvideBody = Body(...)) -> JSONResponse:
        creds = AuthCredentials(
            scheme=body.scheme,
            value=body.value,
            challenge_id=body.challenge_id,
            expires_at=body.expires_at,
            metadata=body.metadata or {},
        )
        try:
            await provide_credentials(
                task_id=task_id,
                credentials=creds,
                store=store,
                registry=registry,
            )
        except A2AError as exc:
            return JSONResponse(
                {
                    "type": f"https://a2a-protocol.org/errors/{exc.error_type}",
                    "title": exc.error_type,
                    "status": exc.http_status,
                    "detail": exc.message,
                },
                status_code=exc.http_status,
                media_type="application/problem+json",
            )
        task = await store.get_task(task_id)
        return JSONResponse(task.model_dump(mode="json", by_alias=True, exclude_none=True))

    @router.get(
        "/tasks/{task_id}/auth:challenge",
        dependencies=[Depends(bearer_dep)] if bearer_dep else [],
        summary="Inspect the active auth challenge for a task (Section 7.6).",
    )
    async def auth_challenge(task_id: str) -> JSONResponse:
        challenge = await registry.get(task_id)
        if challenge is None:
            return JSONResponse(
                {
                    "type": "https://a2a-protocol.org/errors/UnsupportedOperationError",
                    "title": "No active auth challenge",
                    "status": 404,
                    "detail": "Task is not in TASK_STATE_AUTH_REQUIRED or already resolved.",
                },
                status_code=404,
                media_type="application/problem+json",
            )
        return JSONResponse(challenge.to_payload())

    return router


__all__ = [
    "AuthChallenge",
    "AuthCredentials",
    "AuthChallengeRegistry",
    "request_auth",
    "await_auth",
    "provide_credentials",
    "build_auth_router",
]
