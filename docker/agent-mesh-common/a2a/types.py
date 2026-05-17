"""Pydantic models for the A2A v1 wire-format (Section 4 of the spec).

Naming and serialisation rules
==============================

* All models serialise as **camelCase** JSON via Pydantic's
  ``alias_generators.to_camel`` so the wire form matches Section 5.5 and
  the ``a2a.proto`` ProtoJSON mapping verbatim. Internally the Python
  attribute names stay snake_case for ergonomics.
* ``populate_by_name=True`` means the constructors accept either form.
* ``extra="allow"`` keeps the parser permissive against future spec
  additions — unknown fields round-trip through ``model_dump``.
* Enums use the **ProtoJSON canonical form** (``TASK_STATE_*`` /
  ``ROLE_*``). The spec accepts both this form and the JSON-RPC short
  form (``submitted``, ``user``, …); we normalise on the input side via
  :func:`TaskState.coerce` / :func:`Role.coerce` and always emit the
  ProtoJSON variant on output.
* Discriminated unions (``Part``, ``SecurityScheme``,
  ``PushNotificationAuthenticationInfo``) use the ``kind`` /
  ``type`` discriminator named in the spec.

Backwards-compat surface
========================
The previous P0 skeleton (``a2a/types.py`` from earlier scaffolding)
exported ``A2AError``, ``TERMINAL_TASK_STATES`` and
``INTERRUPTED_TASK_STATES``. We keep those re-exports intact so the
existing ``handlers.py`` / ``jsonrpc.py`` modules continue to import
from this file unchanged. The new types are a strict superset.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


# ── helpers ──────────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    """RFC 3339 timestamp with millisecond precision and ``Z`` suffix."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class _A2AModel(BaseModel):
    """Base model: camelCase aliases, allow-extra, snake_case populate."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
        validate_assignment=False,
    )


# ── Section 4.1.5 TaskState ──────────────────────────────────────────────────
class TaskState(str, enum.Enum):
    """ProtoJSON canonical form of ``a2a.v1.TaskState``.

    Section 4.1.5 also defines a JSON-RPC short form
    (``submitted``/``working``/…); :meth:`coerce` accepts either form
    and returns the canonical ProtoJSON value.
    """

    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    COMPLETED = "TASK_STATE_COMPLETED"
    CANCELED = "TASK_STATE_CANCELED"
    FAILED = "TASK_STATE_FAILED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
    # Alias kept for backwards-compat with the earlier skeleton.
    UNKNOWN = "TASK_STATE_UNSPECIFIED"

    @classmethod
    def coerce(cls, value: Any) -> "TaskState":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNSPECIFIED
        s = str(value).strip()
        if not s:
            return cls.UNSPECIFIED
        if s.startswith("TASK_STATE_"):
            try:
                return cls(s)
            except ValueError:
                return cls.UNSPECIFIED
        short = {
            "submitted": cls.SUBMITTED,
            "working": cls.WORKING,
            "input-required": cls.INPUT_REQUIRED,
            "input_required": cls.INPUT_REQUIRED,
            "completed": cls.COMPLETED,
            "canceled": cls.CANCELED,
            "cancelled": cls.CANCELED,
            "failed": cls.FAILED,
            "rejected": cls.REJECTED,
            "auth-required": cls.AUTH_REQUIRED,
            "auth_required": cls.AUTH_REQUIRED,
            "unknown": cls.UNSPECIFIED,
        }
        return short.get(s.lower(), cls.UNSPECIFIED)

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskState.COMPLETED,
            TaskState.CANCELED,
            TaskState.FAILED,
            TaskState.REJECTED,
        }

    @property
    def is_interrupted(self) -> bool:
        """States that pause execution and need a follow-up message."""
        return self in {TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED}


# Backwards-compat exports (used by the earlier skeleton's handlers.py).
TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.CANCELED,
        TaskState.FAILED,
        TaskState.REJECTED,
    }
)
INTERRUPTED_TASK_STATES: frozenset[TaskState] = frozenset(
    {TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED}
)


# ── Section 4.1.4 Role ───────────────────────────────────────────────────────
class Role(str, enum.Enum):
    UNSPECIFIED = "ROLE_UNSPECIFIED"
    USER = "ROLE_USER"
    AGENT = "ROLE_AGENT"

    @classmethod
    def coerce(cls, value: Any) -> "Role":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNSPECIFIED
        s = str(value).strip()
        if s.startswith("ROLE_"):
            try:
                return cls(s)
            except ValueError:
                return cls.UNSPECIFIED
        short = {
            "user": cls.USER,
            "agent": cls.AGENT,
            "assistant": cls.AGENT,
        }
        return short.get(s.lower(), cls.UNSPECIFIED)


# ── Section 4.1.6 Part (discriminated union) ─────────────────────────────────
class FileWithBytes(_A2AModel):
    """``file.bytes`` variant — base64-encoded inline payload."""

    name: str | None = None
    mime_type: str | None = None
    bytes_: str = Field(alias="bytes")


class FileWithUri(_A2AModel):
    """``file.uri`` variant — out-of-band reference."""

    name: str | None = None
    mime_type: str | None = None
    uri: str


FilePayload = FileWithBytes | FileWithUri


class TextPart(_A2AModel):
    kind: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None


class FilePart(_A2AModel):
    kind: Literal["file"] = "file"
    file: FilePayload
    metadata: dict[str, Any] | None = None


class DataPart(_A2AModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


Part = Annotated[
    TextPart | FilePart | DataPart,
    Field(discriminator="kind"),
]


# ── Section 4.1.2 Message ────────────────────────────────────────────────────
class Message(_A2AModel):
    """A2A ``Message`` (Section 4.1.2)."""

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    role: Role = Role.USER
    parts: list[Part] = Field(default_factory=list)
    context_id: str | None = None
    task_id: str | None = None
    reference_task_ids: list[str] | None = None
    extensions: list[str] | None = None
    metadata: dict[str, Any] | None = None
    kind: Literal["message"] = "message"

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, value: Any) -> Any:
        return Role.coerce(value)


# ── Section 4.1.3 Artifact ───────────────────────────────────────────────────
class Artifact(_A2AModel):
    artifact_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str | None = None
    description: str | None = None
    parts: list[Part] = Field(default_factory=list)
    extensions: list[str] | None = None
    metadata: dict[str, Any] | None = None


# ── Section 4.1.7 TaskStatus ─────────────────────────────────────────────────
class TaskStatus(_A2AModel):
    state: TaskState = TaskState.SUBMITTED
    message: Message | None = None
    timestamp: str = Field(default_factory=utc_now_iso)
    # ``error`` is *not* part of A2A v1 TaskStatus, but the earlier
    # skeleton populated it; we keep it as a permissive extension so
    # downstream code that still writes it round-trips cleanly.
    error: dict[str, Any] | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _coerce_state(cls, value: Any) -> Any:
        return TaskState.coerce(value)


# ── Section 4.1.1 Task ───────────────────────────────────────────────────────
class Task(_A2AModel):
    """A2A ``Task`` envelope (Section 4.1.1)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    context_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = Field(default_factory=TaskStatus)
    history: list[Message] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    kind: Literal["task"] = "task"


# ── Section 4.2 Streaming events ─────────────────────────────────────────────
class TaskStatusUpdateEvent(_A2AModel):
    task_id: str
    context_id: str
    status: TaskStatus
    final: bool = False
    metadata: dict[str, Any] | None = None
    kind: Literal["status-update"] = "status-update"


class TaskArtifactUpdateEvent(_A2AModel):
    task_id: str
    context_id: str
    artifact: Artifact
    append: bool = False
    last_chunk: bool = False
    metadata: dict[str, Any] | None = None
    kind: Literal["artifact-update"] = "artifact-update"


# Server-Sent Events stream payload (Section 4.2 / 11.4):
# union of {Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent}.
StreamEvent = Annotated[
    Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    Field(discriminator="kind"),
]


class StreamResponse(_A2AModel):
    """Legacy wire envelope used by SSE streams (Section 4.2)."""

    task: Task | None = None
    status_update: TaskStatusUpdateEvent | None = None
    artifact_update: TaskArtifactUpdateEvent | None = None


# ── Section 4.3 Push notification configs ────────────────────────────────────
class APIKeyAuthenticationInfo(_A2AModel):
    type: Literal["apiKey"] = "apiKey"
    name: str
    in_: Literal["header", "query", "cookie"] = Field("header", alias="in")
    credentials: str | None = None


class HTTPAuthenticationInfo(_A2AModel):
    type: Literal["http"] = "http"
    scheme: str = "bearer"
    bearer_format: str | None = None
    credentials: str | None = None


class OAuth2AuthenticationInfo(_A2AModel):
    type: Literal["oauth2"] = "oauth2"
    flows: dict[str, Any] = Field(default_factory=dict)
    credentials: str | None = None


class OpenIDConnectAuthenticationInfo(_A2AModel):
    type: Literal["openIdConnect"] = "openIdConnect"
    open_id_connect_url: str
    credentials: str | None = None


PushNotificationAuthenticationInfo = Annotated[
    APIKeyAuthenticationInfo
    | HTTPAuthenticationInfo
    | OAuth2AuthenticationInfo
    | OpenIDConnectAuthenticationInfo,
    Field(discriminator="type"),
]


class PushNotificationAuthentication(_A2AModel):
    """Legacy schemas-and-credentials form used by the earlier skeleton.

    Kept for compatibility with code that constructs the simpler shape;
    the canonical type is :data:`PushNotificationAuthenticationInfo`.
    """

    schemes: list[str] = Field(default_factory=lambda: ["bearer"])
    credentials: str | None = None


class PushNotificationConfig(_A2AModel):
    """``PushNotificationConfig`` (Section 4.3)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    url: str
    token: str | None = None
    authentication: (
        PushNotificationAuthenticationInfo | PushNotificationAuthentication | None
    ) = None


class TaskPushNotificationConfig(_A2AModel):
    """Wrapper used by ``tasks/pushNotificationConfig/*`` (Section 9.4)."""

    task_id: str
    push_notification_config: PushNotificationConfig


# ── Section 5 SecurityScheme ─────────────────────────────────────────────────
class APIKeySecurityScheme(_A2AModel):
    type: Literal["apiKey"] = "apiKey"
    name: str
    in_: Literal["query", "header", "cookie"] = Field("header", alias="in")
    description: str | None = None


class HTTPAuthSecurityScheme(_A2AModel):
    type: Literal["http"] = "http"
    scheme: str = "bearer"
    bearer_format: str | None = None
    description: str | None = None


class OAuthFlow(_A2AModel):
    authorization_url: str | None = None
    token_url: str | None = None
    refresh_url: str | None = None
    scopes: dict[str, str] = Field(default_factory=dict)


class OAuthFlows(_A2AModel):
    implicit: OAuthFlow | None = None
    password: OAuthFlow | None = None
    client_credentials: OAuthFlow | None = None
    authorization_code: OAuthFlow | None = None


class OAuth2SecurityScheme(_A2AModel):
    type: Literal["oauth2"] = "oauth2"
    flows: OAuthFlows = Field(default_factory=OAuthFlows)
    description: str | None = None


class OpenIDConnectSecurityScheme(_A2AModel):
    type: Literal["openIdConnect"] = "openIdConnect"
    open_id_connect_url: str
    description: str | None = None


class MutualTLSSecurityScheme(_A2AModel):
    type: Literal["mutualTLS"] = "mutualTLS"
    description: str | None = None


SecurityScheme = Annotated[
    APIKeySecurityScheme
    | HTTPAuthSecurityScheme
    | OAuth2SecurityScheme
    | OpenIDConnectSecurityScheme
    | MutualTLSSecurityScheme,
    Field(discriminator="type"),
]


# ── Section 4.4 AgentCard / AgentSkill / AgentInterface ──────────────────────
class AgentProvider(_A2AModel):
    organization: str
    url: str | None = None


class AgentCapabilities(_A2AModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    extensions: list[str] | None = None


class AgentSkill(_A2AModel):
    """Section 4.4.5 — discoverable capability advertised in the card."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    security_requirements: list[dict[str, list[str]]] | None = None


class AgentInterface(_A2AModel):
    """Section 4.4.4 — one (transport, url) pair."""

    transport: Literal["JSONRPC", "GRPC", "HTTP+JSON", "REST"] = "HTTP+JSON"
    url: str


class AgentCardSignature(_A2AModel):
    """Section 8.4 / 4.4.7 — JWS detached signature over the canonicalized
    Agent Card.

    ``protected`` and ``signature`` are base64url strings as defined by
    RFC 7515. ``header`` is an optional unprotected JOSE header.
    """

    protected: str
    signature: str
    header: dict[str, Any] | None = None


class AgentCard(_A2AModel):
    """Section 4.4 — top-level Agent Card document."""

    name: str
    description: str = ""
    url: str
    icon_url: str | None = None
    provider: AgentProvider | None = None
    version: str = "0.0.1"
    documentation_url: str | None = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    security_schemes: dict[str, SecurityScheme] | None = None
    security: list[dict[str, list[str]]] | None = None
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"]
    )
    skills: list[AgentSkill] = Field(default_factory=list)
    additional_interfaces: list[AgentInterface] | None = None
    preferred_transport: Literal["JSONRPC", "GRPC", "HTTP+JSON", "REST"] = "HTTP+JSON"
    supports_authenticated_extended_card: bool = False
    protocol_version: str = "0.3.0"
    signatures: list[AgentCardSignature] | None = None
    # Free-form ``id`` field accepted by some legacy callers; the spec
    # itself has no ``id`` on AgentCard but we keep it as an opt-in.
    id: str | None = None
    metadata: dict[str, Any] | None = None
    interfaces: list[AgentInterface] | None = None


# ── Section 5.4 A2A error mapping ────────────────────────────────────────────
A2A_ERROR_CODES: dict[str, int] = {
    "TaskNotFoundError": -32001,
    "TaskNotCancelableError": -32002,
    "PushNotificationNotSupportedError": -32003,
    "UnsupportedOperationError": -32004,
    "ContentTypeNotSupportedError": -32005,
    "InvalidAgentResponseError": -32006,
    "AuthenticatedExtendedCardNotConfiguredError": -32007,
    "InvalidRequestError": -32600,
    "MethodNotFoundError": -32601,
    "InvalidParamsError": -32602,
    "InternalError": -32603,
}

A2A_ERROR_HTTP_STATUS: dict[str, int] = {
    "TaskNotFoundError": 404,
    "TaskNotCancelableError": 409,
    "PushNotificationNotSupportedError": 400,
    "UnsupportedOperationError": 400,
    "ContentTypeNotSupportedError": 415,
    "InvalidAgentResponseError": 502,
    "AuthenticatedExtendedCardNotConfiguredError": 404,
    "InvalidRequestError": 400,
    "MethodNotFoundError": 404,
    "InvalidParamsError": 400,
    "InternalError": 500,
}


class A2AError(Exception):
    """Server-side error that propagates through every binding.

    Bindings translate to RFC 7807 (REST), JSON-RPC ``error`` object, or a
    gRPC ``StatusCode``; the *name* (``error_type``) is the source of
    truth — see :data:`A2A_ERROR_CODES` / :data:`A2A_ERROR_HTTP_STATUS`.
    """

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.data = data

    @property
    def code(self) -> int:
        return A2A_ERROR_CODES.get(self.error_type, -32603)

    @property
    def http_status(self) -> int:
        return A2A_ERROR_HTTP_STATUS.get(self.error_type, 500)

    def to_problem_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": f"https://a2a-protocol.org/errors/{self.error_type}",
            "title": self.error_type,
            "status": self.http_status,
            "detail": self.message,
        }
        if self.data is not None:
            out["data"] = self.data
        return out

    def to_jsonrpc_error(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


__all__ = [
    "AgentCapabilities",
    "AgentCard",
    "AgentCardSignature",
    "AgentInterface",
    "AgentProvider",
    "AgentSkill",
    "Artifact",
    "DataPart",
    "FilePart",
    "FileWithBytes",
    "FileWithUri",
    "FilePayload",
    "Message",
    "Part",
    "PushNotificationAuthentication",
    "PushNotificationAuthenticationInfo",
    "PushNotificationConfig",
    "Role",
    "SecurityScheme",
    "APIKeySecurityScheme",
    "HTTPAuthSecurityScheme",
    "OAuth2SecurityScheme",
    "OpenIDConnectSecurityScheme",
    "MutualTLSSecurityScheme",
    "StreamEvent",
    "StreamResponse",
    "Task",
    "TaskArtifactUpdateEvent",
    "TaskPushNotificationConfig",
    "TaskState",
    "TaskStatus",
    "TaskStatusUpdateEvent",
    "TextPart",
    "TERMINAL_TASK_STATES",
    "INTERRUPTED_TASK_STATES",
    "A2A_ERROR_CODES",
    "A2A_ERROR_HTTP_STATUS",
    "A2AError",
    "utc_now_iso",
]
