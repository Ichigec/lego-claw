"""A2A protocol implementation for the agent-mesh adapters.

Public surface mirrors the A2A v1 specification (a2aproject/A2A). The
package is organised as:

* :mod:`.types` — Pydantic models matching Section 4/5 of the spec.
* :mod:`.task_store` — :class:`TaskStore` ABC + in-memory and Postgres
  implementations.
* :mod:`.rest` — FastAPI router exposing ``/a2a/v1/*``.
* :mod:`.agent_card` — Agent Card builder (used by the well-known
  endpoints and ``/v1/extendedAgentCard``).
* :mod:`.jws` — RFC 8785 / RFC 7515 (JWS over canonicalized JSON)
  helpers used to sign Agent Cards.

The two adapters (``clawcode-adapter``, ``openhands-adapter``) wire this
package in via :func:`build_app` (``agent_mesh_adapter.py``).
"""
from __future__ import annotations

from .types import (
    A2AError,
    A2A_ERROR_CODES,
    A2A_ERROR_HTTP_STATUS,
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    INTERRUPTED_TASK_STATES,
    Message,
    Part,
    PushNotificationAuthentication,
    PushNotificationAuthenticationInfo,
    PushNotificationConfig,
    Role,
    SecurityScheme,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TERMINAL_TASK_STATES,
    TextPart,
    utc_now_iso,
)

__all__ = [
    "A2AError",
    "A2A_ERROR_CODES",
    "A2A_ERROR_HTTP_STATUS",
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
    "INTERRUPTED_TASK_STATES",
    "Message",
    "Part",
    "PushNotificationAuthentication",
    "PushNotificationAuthenticationInfo",
    "PushNotificationConfig",
    "Role",
    "SecurityScheme",
    "StreamResponse",
    "Task",
    "TaskArtifactUpdateEvent",
    "TaskState",
    "TaskStatus",
    "TaskStatusUpdateEvent",
    "TERMINAL_TASK_STATES",
    "TextPart",
    "utc_now_iso",
]
