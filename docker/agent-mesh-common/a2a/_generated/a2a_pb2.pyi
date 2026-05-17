import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TaskState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATE_UNSPECIFIED: _ClassVar[TaskState]
    TASK_STATE_SUBMITTED: _ClassVar[TaskState]
    TASK_STATE_WORKING: _ClassVar[TaskState]
    TASK_STATE_INPUT_REQUIRED: _ClassVar[TaskState]
    TASK_STATE_AUTH_REQUIRED: _ClassVar[TaskState]
    TASK_STATE_COMPLETED: _ClassVar[TaskState]
    TASK_STATE_CANCELED: _ClassVar[TaskState]
    TASK_STATE_FAILED: _ClassVar[TaskState]
    TASK_STATE_REJECTED: _ClassVar[TaskState]
    TASK_STATE_UNKNOWN: _ClassVar[TaskState]

class Role(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ROLE_UNSPECIFIED: _ClassVar[Role]
    ROLE_USER: _ClassVar[Role]
    ROLE_AGENT: _ClassVar[Role]
TASK_STATE_UNSPECIFIED: TaskState
TASK_STATE_SUBMITTED: TaskState
TASK_STATE_WORKING: TaskState
TASK_STATE_INPUT_REQUIRED: TaskState
TASK_STATE_AUTH_REQUIRED: TaskState
TASK_STATE_COMPLETED: TaskState
TASK_STATE_CANCELED: TaskState
TASK_STATE_FAILED: TaskState
TASK_STATE_REJECTED: TaskState
TASK_STATE_UNKNOWN: TaskState
ROLE_UNSPECIFIED: Role
ROLE_USER: Role
ROLE_AGENT: Role

class TextPart(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...

class RawPart(_message.Message):
    __slots__ = ("mime_type", "data")
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    mime_type: str
    data: bytes
    def __init__(self, mime_type: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class UrlPart(_message.Message):
    __slots__ = ("mime_type", "url")
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    mime_type: str
    url: str
    def __init__(self, mime_type: _Optional[str] = ..., url: _Optional[str] = ...) -> None: ...

class DataPart(_message.Message):
    __slots__ = ("mime_type", "data")
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    mime_type: str
    data: _struct_pb2.Struct
    def __init__(self, mime_type: _Optional[str] = ..., data: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Part(_message.Message):
    __slots__ = ("text", "raw", "url", "data")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    RAW_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    text: TextPart
    raw: RawPart
    url: UrlPart
    data: DataPart
    def __init__(self, text: _Optional[_Union[TextPart, _Mapping]] = ..., raw: _Optional[_Union[RawPart, _Mapping]] = ..., url: _Optional[_Union[UrlPart, _Mapping]] = ..., data: _Optional[_Union[DataPart, _Mapping]] = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ("message_id", "role", "parts", "context_id", "metadata")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    PARTS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    role: Role
    parts: _containers.RepeatedCompositeFieldContainer[Part]
    context_id: str
    metadata: _struct_pb2.Struct
    def __init__(self, message_id: _Optional[str] = ..., role: _Optional[_Union[Role, str]] = ..., parts: _Optional[_Iterable[_Union[Part, _Mapping]]] = ..., context_id: _Optional[str] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Artifact(_message.Message):
    __slots__ = ("artifact_id", "name", "description", "parts", "metadata")
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    PARTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    name: str
    description: str
    parts: _containers.RepeatedCompositeFieldContainer[Part]
    metadata: _struct_pb2.Struct
    def __init__(self, artifact_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., parts: _Optional[_Iterable[_Union[Part, _Mapping]]] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class TaskStatus(_message.Message):
    __slots__ = ("state", "timestamp", "message", "error")
    STATE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    state: TaskState
    timestamp: _timestamp_pb2.Timestamp
    message: Message
    error: _struct_pb2.Struct
    def __init__(self, state: _Optional[_Union[TaskState, str]] = ..., timestamp: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., message: _Optional[_Union[Message, _Mapping]] = ..., error: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Task(_message.Message):
    __slots__ = ("id", "context_id", "status", "history", "artifacts", "metadata")
    ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    HISTORY_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    context_id: str
    status: TaskStatus
    history: _containers.RepeatedCompositeFieldContainer[Message]
    artifacts: _containers.RepeatedCompositeFieldContainer[Artifact]
    metadata: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., context_id: _Optional[str] = ..., status: _Optional[_Union[TaskStatus, _Mapping]] = ..., history: _Optional[_Iterable[_Union[Message, _Mapping]]] = ..., artifacts: _Optional[_Iterable[_Union[Artifact, _Mapping]]] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class TaskStatusUpdateEvent(_message.Message):
    __slots__ = ("task_id", "context_id", "status", "final", "metadata")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FINAL_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    context_id: str
    status: TaskStatus
    final: bool
    metadata: _struct_pb2.Struct
    def __init__(self, task_id: _Optional[str] = ..., context_id: _Optional[str] = ..., status: _Optional[_Union[TaskStatus, _Mapping]] = ..., final: bool = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class TaskArtifactUpdateEvent(_message.Message):
    __slots__ = ("task_id", "context_id", "artifact", "append", "last_chunk", "metadata")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    APPEND_FIELD_NUMBER: _ClassVar[int]
    LAST_CHUNK_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    context_id: str
    artifact: Artifact
    append: bool
    last_chunk: bool
    metadata: _struct_pb2.Struct
    def __init__(self, task_id: _Optional[str] = ..., context_id: _Optional[str] = ..., artifact: _Optional[_Union[Artifact, _Mapping]] = ..., append: bool = ..., last_chunk: bool = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class StreamResponse(_message.Message):
    __slots__ = ("task", "status_update", "artifact_update")
    TASK_FIELD_NUMBER: _ClassVar[int]
    STATUS_UPDATE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_UPDATE_FIELD_NUMBER: _ClassVar[int]
    task: Task
    status_update: TaskStatusUpdateEvent
    artifact_update: TaskArtifactUpdateEvent
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ..., status_update: _Optional[_Union[TaskStatusUpdateEvent, _Mapping]] = ..., artifact_update: _Optional[_Union[TaskArtifactUpdateEvent, _Mapping]] = ...) -> None: ...

class PushNotificationAuthentication(_message.Message):
    __slots__ = ("schemes", "credentials")
    SCHEMES_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    schemes: _containers.RepeatedScalarFieldContainer[str]
    credentials: str
    def __init__(self, schemes: _Optional[_Iterable[str]] = ..., credentials: _Optional[str] = ...) -> None: ...

class PushNotificationConfig(_message.Message):
    __slots__ = ("id", "url", "token", "authentication")
    ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    AUTHENTICATION_FIELD_NUMBER: _ClassVar[int]
    id: str
    url: str
    token: str
    authentication: PushNotificationAuthentication
    def __init__(self, id: _Optional[str] = ..., url: _Optional[str] = ..., token: _Optional[str] = ..., authentication: _Optional[_Union[PushNotificationAuthentication, _Mapping]] = ...) -> None: ...

class AgentInterface(_message.Message):
    __slots__ = ("transport", "url")
    TRANSPORT_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    transport: str
    url: str
    def __init__(self, transport: _Optional[str] = ..., url: _Optional[str] = ...) -> None: ...

class AgentCapabilities(_message.Message):
    __slots__ = ("streaming", "push_notifications", "state_transition_history")
    STREAMING_FIELD_NUMBER: _ClassVar[int]
    PUSH_NOTIFICATIONS_FIELD_NUMBER: _ClassVar[int]
    STATE_TRANSITION_HISTORY_FIELD_NUMBER: _ClassVar[int]
    streaming: bool
    push_notifications: bool
    state_transition_history: bool
    def __init__(self, streaming: bool = ..., push_notifications: bool = ..., state_transition_history: bool = ...) -> None: ...

class AgentSkill(_message.Message):
    __slots__ = ("id", "name", "description", "tags", "examples", "input_modes", "output_modes")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    EXAMPLES_FIELD_NUMBER: _ClassVar[int]
    INPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    description: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    examples: _containers.RepeatedScalarFieldContainer[str]
    input_modes: _containers.RepeatedScalarFieldContainer[str]
    output_modes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., examples: _Optional[_Iterable[str]] = ..., input_modes: _Optional[_Iterable[str]] = ..., output_modes: _Optional[_Iterable[str]] = ...) -> None: ...

class AgentCard(_message.Message):
    __slots__ = ("id", "name", "description", "version", "protocol_version", "url", "capabilities", "interfaces", "skills", "default_input_modes", "default_output_modes", "metadata")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    INTERFACES_FIELD_NUMBER: _ClassVar[int]
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_INPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_OUTPUT_MODES_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    description: str
    version: str
    protocol_version: str
    url: str
    capabilities: AgentCapabilities
    interfaces: _containers.RepeatedCompositeFieldContainer[AgentInterface]
    skills: _containers.RepeatedCompositeFieldContainer[AgentSkill]
    default_input_modes: _containers.RepeatedScalarFieldContainer[str]
    default_output_modes: _containers.RepeatedScalarFieldContainer[str]
    metadata: _struct_pb2.Struct
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., version: _Optional[str] = ..., protocol_version: _Optional[str] = ..., url: _Optional[str] = ..., capabilities: _Optional[_Union[AgentCapabilities, _Mapping]] = ..., interfaces: _Optional[_Iterable[_Union[AgentInterface, _Mapping]]] = ..., skills: _Optional[_Iterable[_Union[AgentSkill, _Mapping]]] = ..., default_input_modes: _Optional[_Iterable[str]] = ..., default_output_modes: _Optional[_Iterable[str]] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class SendMessageRequest(_message.Message):
    __slots__ = ("message", "context_id", "return_immediately")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    RETURN_IMMEDIATELY_FIELD_NUMBER: _ClassVar[int]
    message: Message
    context_id: str
    return_immediately: bool
    def __init__(self, message: _Optional[_Union[Message, _Mapping]] = ..., context_id: _Optional[str] = ..., return_immediately: bool = ...) -> None: ...

class StreamMessageRequest(_message.Message):
    __slots__ = ("message", "context_id")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    message: Message
    context_id: str
    def __init__(self, message: _Optional[_Union[Message, _Mapping]] = ..., context_id: _Optional[str] = ...) -> None: ...

class GetTaskRequest(_message.Message):
    __slots__ = ("id", "history_length")
    ID_FIELD_NUMBER: _ClassVar[int]
    HISTORY_LENGTH_FIELD_NUMBER: _ClassVar[int]
    id: str
    history_length: int
    def __init__(self, id: _Optional[str] = ..., history_length: _Optional[int] = ...) -> None: ...

class ListTasksRequest(_message.Message):
    __slots__ = ("context_id", "status", "page_size", "page_token", "status_timestamp_after", "include_artifacts")
    CONTEXT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    STATUS_TIMESTAMP_AFTER_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    context_id: str
    status: TaskState
    page_size: int
    page_token: str
    status_timestamp_after: float
    include_artifacts: bool
    def __init__(self, context_id: _Optional[str] = ..., status: _Optional[_Union[TaskState, str]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., status_timestamp_after: _Optional[float] = ..., include_artifacts: bool = ...) -> None: ...

class ListTasksResponse(_message.Message):
    __slots__ = ("tasks", "next_page_token")
    TASKS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[Task]
    next_page_token: str
    def __init__(self, tasks: _Optional[_Iterable[_Union[Task, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class CancelTaskRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class ResubscribeRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class GetAgentCardRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class PushConfigRequest(_message.Message):
    __slots__ = ("task_id", "config")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    config: PushNotificationConfig
    def __init__(self, task_id: _Optional[str] = ..., config: _Optional[_Union[PushNotificationConfig, _Mapping]] = ...) -> None: ...

class PushConfigQuery(_message.Message):
    __slots__ = ("task_id", "config_id")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIG_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    config_id: str
    def __init__(self, task_id: _Optional[str] = ..., config_id: _Optional[str] = ...) -> None: ...

class ListPushConfigsResponse(_message.Message):
    __slots__ = ("configs",)
    CONFIGS_FIELD_NUMBER: _ClassVar[int]
    configs: _containers.RepeatedCompositeFieldContainer[PushNotificationConfig]
    def __init__(self, configs: _Optional[_Iterable[_Union[PushNotificationConfig, _Mapping]]] = ...) -> None: ...

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
