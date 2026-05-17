"""A2A gRPC server (Section 10 of the spec).

Exposes the same set of methods as the JSON-RPC and REST bindings, on a
dedicated port (defaults: clawcode-adapter → ``:8796``, openhands-adapter →
``:8797``).  Reuses the shared handlers in :mod:`.handlers`, so semantics
match byte-for-byte across bindings.

The optional dependencies (``grpcio``, ``grpcio-tools``, and the generated
``a2a_pb2`` stubs) are loaded lazily so an adapter image that ships
without gRPC support degrades gracefully — :func:`start_grpc_server`
becomes a no-op and the REST/JSON-RPC surface stays available.

Run-time wiring (see :mod:`agent_mesh_adapter.build_app`):

.. code-block:: python

    from a2a.grpc_server import start_grpc_server
    grpc_handle = await start_grpc_server(
        host="0.0.0.0",
        port=int(os.environ.get("A2A_GRPC_PORT", "0") or 0),
        store=store,
        runner=a2a_runner,
        bearer=config.api_key,
    )

``grpc_handle`` is ``None`` if gRPC was disabled (port=0) or imports
failed; otherwise it is the started :class:`grpc.aio.Server`. The caller
should ``await grpc_handle.stop(grace)`` from the FastAPI lifespan
shutdown hook.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .handlers import (
    RunnerLike,
    cancel_task,
    delete_push_config,
    get_task,
    list_push_configs,
    list_tasks,
    set_push_config,
    stream_message,
    submit_message,
    subscribe_task,
)
from .task_store import InMemoryTaskStore
from .types import (
    A2AError,
    Message,
    PushNotificationConfig,
    Role,
    TaskState,
)

logger = logging.getLogger("agent_mesh_adapter.a2a.grpc")


def _try_import_grpc():
    """Lazy import the optional gRPC dependencies.

    Returns ``(grpc, aio, pb2, pb2_grpc)`` on success or ``None`` if the
    image was built without gRPC support.
    """
    try:
        import grpc  # type: ignore
        from grpc import aio  # type: ignore
    except ImportError:
        logger.info("grpcio not installed; gRPC binding disabled")
        return None
    try:
        from ._generated import a2a_pb2, a2a_pb2_grpc  # type: ignore
    except ImportError:
        logger.warning(
            "a2a gRPC stubs not generated (run proto/regen.sh); "
            "gRPC binding disabled"
        )
        return None
    return grpc, aio, a2a_pb2, a2a_pb2_grpc


# ── Conversion helpers (Pydantic ↔ protobuf) ────────────────────────────────
def _struct_to_dict(pb_struct) -> dict[str, Any]:
    """Convert ``google.protobuf.Struct`` → plain dict."""
    try:
        from google.protobuf.json_format import MessageToDict
    except ImportError:  # pragma: no cover
        return {}
    return MessageToDict(pb_struct, preserving_proto_field_name=True) if pb_struct else {}


def _dict_to_struct(d: dict[str, Any]):
    from google.protobuf.struct_pb2 import Struct  # type: ignore

    s = Struct()
    if d:
        s.update(d)
    return s


def _pb_role_to_str(pb_role) -> str:
    mapping = {0: "ROLE_USER", 1: "ROLE_USER", 2: "ROLE_AGENT"}
    return mapping.get(int(pb_role), "ROLE_USER")


def _pb_part_to_part(pb_part) -> dict[str, Any]:
    """Convert a protobuf ``Part`` oneof into the A2A spec dict shape.

    The spec uses a discriminated union keyed on ``kind`` (``text`` |
    ``file`` | ``data``) — see Section 4.1.6 of the A2A spec and the
    Pydantic models in :mod:`.types`.  The vendored proto exposes a
    superset (``raw`` / ``url``); both translate into the canonical
    ``file`` variant on the JSON side.
    """
    kind = pb_part.WhichOneof("kind")
    if kind == "text":
        return {"kind": "text", "text": pb_part.text.text}
    if kind == "raw":
        return {
            "kind": "file",
            "file": {
                "mimeType": pb_part.raw.mime_type,
                "bytes": pb_part.raw.data.decode("utf-8", errors="replace"),
            },
        }
    if kind == "url":
        return {
            "kind": "file",
            "file": {
                "mimeType": pb_part.url.mime_type,
                "uri": pb_part.url.url,
            },
        }
    if kind == "data":
        return {
            "kind": "data",
            "data": _struct_to_dict(pb_part.data.data),
        }
    return {}


def _pb_message_to_message(pb_msg) -> Message:
    payload: dict[str, Any] = {
        "role": _pb_role_to_str(pb_msg.role),
        "parts": [_pb_part_to_part(p) for p in pb_msg.parts],
    }
    # Only include optional string fields when actually populated — proto3
    # defaults to "" but the Pydantic model uses default_factory/None.
    if pb_msg.message_id:
        payload["messageId"] = pb_msg.message_id
    if pb_msg.context_id:
        payload["contextId"] = pb_msg.context_id
    md = _struct_to_dict(pb_msg.metadata)
    if md:
        payload["metadata"] = md
    return Message.model_validate(payload)


def _task_to_pb(task, pb2):  # noqa: ANN001
    """Best-effort Pydantic Task → protobuf Task.

    We marshal through dict to keep the converter compact; non-critical
    fields fall back to defaults when the proto schema diverges.
    """
    from google.protobuf.json_format import ParseDict

    payload = task.model_dump(by_alias=True, exclude_none=True)
    # Proto field names are snake_case; pydantic emits camelCase.  ParseDict
    # accepts either when ignore_unknown_fields=True, so just feed it.
    pb_task = pb2.Task()
    try:
        ParseDict(payload, pb_task, ignore_unknown_fields=True)
    except Exception:  # pragma: no cover
        logger.exception("failed to marshal Task to proto; returning bare envelope")
    return pb_task


def _push_cfg_to_pb(cfg, pb2):  # noqa: ANN001
    pb = pb2.PushNotificationConfig(
        id=cfg.id,
        url=cfg.url,
        token=cfg.token or "",
    )
    if cfg.authentication:
        pb.authentication.schemes.extend(cfg.authentication.schemes)
        if cfg.authentication.credentials:
            pb.authentication.credentials = cfg.authentication.credentials
    return pb


def _pb_to_push_cfg(pb) -> PushNotificationConfig:  # noqa: ANN001
    auth = None
    if pb.HasField("authentication"):
        auth = {
            "schemes": list(pb.authentication.schemes),
            "credentials": pb.authentication.credentials or None,
        }
    payload: dict[str, Any] = {"url": pb.url}
    if pb.id:
        payload["id"] = pb.id
    if pb.token:
        payload["token"] = pb.token
    if auth:
        payload["authentication"] = auth
    return PushNotificationConfig.model_validate(payload)


# ── Servicer ────────────────────────────────────────────────────────────────
def _build_servicer(*, store: InMemoryTaskStore, runner: RunnerLike, bearer: str | None):
    bundle = _try_import_grpc()
    if bundle is None:
        return None
    grpc, _, pb2, pb2_grpc = bundle

    def _check_auth(context) -> bool:
        if not bearer:
            return True
        for key, value in context.invocation_metadata():
            if key.lower() == "authorization" and value.lower().startswith("bearer "):
                if value.split(" ", 1)[1].strip() == bearer:
                    return True
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("Bearer token required")
        return False

    async def _raise_a2a(context, exc: A2AError):
        code = grpc.StatusCode.INTERNAL
        if exc.error_type == "TaskNotFoundError":
            code = grpc.StatusCode.NOT_FOUND
        elif exc.error_type in ("InvalidParamsError", "InvalidRequestError"):
            code = grpc.StatusCode.INVALID_ARGUMENT
        elif exc.error_type == "TaskNotCancelableError":
            code = grpc.StatusCode.FAILED_PRECONDITION
        elif exc.error_type in ("UnsupportedOperationError",
                                "PushNotificationNotSupportedError"):
            code = grpc.StatusCode.UNIMPLEMENTED
        elif exc.error_type == "ContentTypeNotSupportedError":
            code = grpc.StatusCode.INVALID_ARGUMENT
        elif exc.error_type == "InvalidAgentResponseError":
            code = grpc.StatusCode.INTERNAL
        await context.abort(code, f"{exc.error_type}: {exc.message}")

    class A2AServicer(pb2_grpc.A2AServiceServicer):  # type: ignore[misc, name-defined]
        async def SendMessage(self, request, context):  # noqa: N802 (gRPC naming)
            if not _check_auth(context):
                return pb2.Task()
            try:
                msg = _pb_message_to_message(request.message)
                task = await submit_message(
                    store=store,
                    runner=runner,
                    message=msg,
                    context_id=request.context_id or None,
                    return_immediately=bool(request.return_immediately),
                    depth=1,
                )
                return _task_to_pb(task, pb2)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.Task()

        async def StreamMessage(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return
            try:
                msg = _pb_message_to_message(request.message)
                events = await stream_message(
                    store=store,
                    runner=runner,
                    message=msg,
                    context_id=request.context_id or None,
                    depth=1,
                )
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return
            async for evt in events:
                resp = pb2.StreamResponse()
                if "task" in evt:
                    resp.task.CopyFrom(_task_to_pb(
                        type("T", (), {"model_dump": lambda self, **k: evt["task"]})(),
                        pb2,
                    ))
                elif "statusUpdate" in evt:
                    from google.protobuf.json_format import ParseDict

                    ParseDict(evt["statusUpdate"], resp.status_update, ignore_unknown_fields=True)
                elif "artifactUpdate" in evt:
                    from google.protobuf.json_format import ParseDict

                    ParseDict(evt["artifactUpdate"], resp.artifact_update, ignore_unknown_fields=True)
                yield resp

        async def GetTask(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.Task()
            try:
                task = await get_task(
                    store=store,
                    task_id=request.id,
                    history_length=request.history_length or None,
                )
                return _task_to_pb(task, pb2)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.Task()

        async def ListTasks(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.ListTasksResponse()
            try:
                result = await list_tasks(
                    store=store,
                    context_id=request.context_id or None,
                    status=TaskState(request.status).value if request.status else None,
                    page_size=request.page_size or 50,
                    page_token=request.page_token or "",
                    status_timestamp_after=request.status_timestamp_after or None,
                    include_artifacts=bool(request.include_artifacts),
                )
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.ListTasksResponse()
            resp = pb2.ListTasksResponse(next_page_token=result["nextPageToken"])
            for raw in result["tasks"]:
                pb_task = pb2.Task()
                from google.protobuf.json_format import ParseDict

                ParseDict(raw, pb_task, ignore_unknown_fields=True)
                resp.tasks.append(pb_task)
            return resp

        async def CancelTask(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.Task()
            try:
                task = await cancel_task(store=store, task_id=request.id)
                return _task_to_pb(task, pb2)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.Task()

        async def ResubscribeToTask(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return
            try:
                events = await subscribe_task(store=store, task_id=request.id)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return
            async for evt in events:
                resp = pb2.StreamResponse()
                from google.protobuf.json_format import ParseDict

                if "task" in evt:
                    ParseDict(evt["task"], resp.task, ignore_unknown_fields=True)
                elif "statusUpdate" in evt:
                    ParseDict(evt["statusUpdate"], resp.status_update, ignore_unknown_fields=True)
                elif "artifactUpdate" in evt:
                    ParseDict(evt["artifactUpdate"], resp.artifact_update, ignore_unknown_fields=True)
                yield resp

        async def SetPushNotificationConfig(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.PushNotificationConfig()
            try:
                cfg = _pb_to_push_cfg(request.config)
                saved = await set_push_config(store=store, task_id=request.task_id, cfg=cfg)
                return _push_cfg_to_pb(saved, pb2)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.PushNotificationConfig()

        async def GetPushNotificationConfig(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.PushNotificationConfig()
            try:
                cfgs = await list_push_configs(store=store, task_id=request.task_id)
                for c in cfgs:
                    if c.id == request.config_id:
                        return _push_cfg_to_pb(c, pb2)
                await _raise_a2a(
                    context,
                    A2AError("TaskNotFoundError", f"push config {request.config_id} not found"),
                )
            except A2AError as exc:
                await _raise_a2a(context, exc)
            return pb2.PushNotificationConfig()

        async def ListPushNotificationConfigs(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.ListPushConfigsResponse()
            try:
                cfgs = await list_push_configs(store=store, task_id=request.task_id)
            except A2AError as exc:
                await _raise_a2a(context, exc)
                return pb2.ListPushConfigsResponse()
            resp = pb2.ListPushConfigsResponse()
            for c in cfgs:
                resp.configs.append(_push_cfg_to_pb(c, pb2))
            return resp

        async def DeletePushNotificationConfig(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.Empty()
            try:
                await delete_push_config(
                    store=store,
                    task_id=request.task_id,
                    cfg_id=request.config_id,
                )
            except A2AError as exc:
                await _raise_a2a(context, exc)
            return pb2.Empty()

        async def GetAgentCard(self, request, context):  # noqa: N802
            if not _check_auth(context):
                return pb2.AgentCard()
            card_provider = getattr(store, "agent_card", None)
            if not callable(card_provider):
                await _raise_a2a(
                    context,
                    A2AError(
                        "UnsupportedOperationError",
                        "agent card not wired; fetch /.well-known/agent-card.json",
                    ),
                )
                return pb2.AgentCard()
            card = card_provider()
            pb_card = pb2.AgentCard()
            try:
                from google.protobuf.json_format import ParseDict

                ParseDict(card.model_dump(by_alias=True, exclude_none=True),
                          pb_card, ignore_unknown_fields=True)
            except Exception:  # pragma: no cover
                logger.exception("failed to marshal AgentCard to proto")
            return pb_card

    return A2AServicer(), pb2_grpc


# ── Public entry-point ──────────────────────────────────────────────────────
async def start_grpc_server(
    *,
    host: str,
    port: int,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    bearer: str | None,
):
    """Start an aio gRPC server bound to ``host:port``.

    Returns the :class:`grpc.aio.Server` instance or ``None`` if gRPC is
    disabled (port ≤ 0 or optional deps missing).  The caller is responsible
    for awaiting ``server.stop(grace)`` from the lifespan shutdown.
    """
    if not port or port <= 0:
        logger.info("A2A_GRPC_PORT not set; skipping gRPC binding")
        return None

    bundle = _try_import_grpc()
    if bundle is None:
        return None
    grpc, aio, pb2, pb2_grpc = bundle

    built = _build_servicer(store=store, runner=runner, bearer=bearer)
    if built is None:
        return None
    servicer, pb2_grpc_mod = built

    server = aio.server()
    pb2_grpc_mod.add_A2AServiceServicer_to_server(servicer, server)
    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info("A2A gRPC server listening on %s", listen_addr)
    return server


__all__ = ["start_grpc_server"]
