"""A2A JSON-RPC 2.0 binding (Section 9 of the spec).

A single FastAPI route — ``POST /a2a/jsonrpc`` — accepts:

* a single Request object, or
* a batch (array) of Request objects (Section 9.5).

Method names follow Section 9.4 (``message/send``, ``message/stream``,
``tasks/get``, ``tasks/list``, ``tasks/cancel``, ``tasks/resubscribe``,
``tasks/pushNotificationConfig/set|get|list|delete``).

Every method is implemented by delegating to :mod:`.handlers`, which is the
same module REST and gRPC bindings call.  This guarantees byte-for-byte
identical semantics across bindings — the only thing this module owns is
the JSON-RPC envelope and the SSE format for the streaming methods.

Errors map to JSON-RPC error objects:

* parsed but unknown method        → ``-32601 MethodNotFoundError``
* missing/invalid params           → ``-32602 InvalidParamsError``
* malformed payload                → ``-32600 InvalidRequestError``
* :class:`.types.A2AError` raised  → ``.code``/``.message``/``.data``
* anything else                    → ``-32603 InternalError``

The streaming methods (``message/stream`` and ``tasks/resubscribe``) cannot
be tunnelled through a single Response, so per spec we keep the connection
open and emit ``text/event-stream`` SSE frames — each frame is a complete
JSON-RPC response carrying one event from the result stream.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

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
    A2A_ERROR_CODES,
    A2AError,
    Message,
    PushNotificationConfig,
)

logger = logging.getLogger("agent_mesh_adapter.a2a.jsonrpc")


# ── JSON-RPC envelope helpers ───────────────────────────────────────────────
def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, *, data: Any | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _err_from(req_id: Any, exc: A2AError) -> dict[str, Any]:
    return _err(req_id, exc.code, exc.message, data=exc.data)


# ── Dispatcher ──────────────────────────────────────────────────────────────
STREAMING_METHODS = frozenset({"message/stream", "tasks/resubscribe"})


async def _dispatch_unary(
    *,
    method: str,
    params: dict[str, Any],
    req_id: Any,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    depth: int,
) -> dict[str, Any]:
    """Run a non-streaming method and wrap the result as a JSON-RPC reply."""
    try:
        if method == "message/send":
            msg_raw = params.get("message")
            if not isinstance(msg_raw, dict):
                raise A2AError("InvalidParamsError", "message: object required")
            message = Message.model_validate(msg_raw)
            return_immediately = bool(params.get("returnImmediately", False))
            task = await submit_message(
                store=store,
                runner=runner,
                message=message,
                context_id=params.get("contextId"),
                return_immediately=return_immediately,
                depth=depth,
            )
            return _ok(req_id, task.model_dump(by_alias=True, exclude_none=True))

        if method == "tasks/get":
            task_id = params.get("id") or params.get("taskId")
            if not isinstance(task_id, str) or not task_id:
                raise A2AError("InvalidParamsError", "id: string required")
            history_length = params.get("historyLength")
            task = await get_task(store=store, task_id=task_id, history_length=history_length)
            return _ok(req_id, task.model_dump(by_alias=True, exclude_none=True))

        if method == "tasks/list":
            result = await list_tasks(
                store=store,
                context_id=params.get("contextId"),
                status=params.get("status"),
                page_size=int(params.get("pageSize") or 50),
                page_token=str(params.get("pageToken") or ""),
                status_timestamp_after=params.get("statusTimestampAfter"),
                include_artifacts=bool(params.get("includeArtifacts", True)),
            )
            return _ok(req_id, result)

        if method == "tasks/cancel":
            task_id = params.get("id") or params.get("taskId")
            if not isinstance(task_id, str) or not task_id:
                raise A2AError("InvalidParamsError", "id: string required")
            task = await cancel_task(store=store, task_id=task_id)
            return _ok(req_id, task.model_dump(by_alias=True, exclude_none=True))

        if method == "tasks/pushNotificationConfig/set":
            task_id = params.get("taskId") or params.get("id")
            cfg_raw = params.get("pushNotificationConfig") or params.get("config")
            if not isinstance(task_id, str) or not isinstance(cfg_raw, dict):
                raise A2AError("InvalidParamsError", "taskId + pushNotificationConfig required")
            cfg = PushNotificationConfig.model_validate(cfg_raw)
            saved = await set_push_config(store=store, task_id=task_id, cfg=cfg)
            return _ok(req_id, saved.model_dump(by_alias=True, exclude_none=True))

        if method == "tasks/pushNotificationConfig/list":
            task_id = params.get("taskId") or params.get("id")
            if not isinstance(task_id, str):
                raise A2AError("InvalidParamsError", "taskId required")
            cfgs = await list_push_configs(store=store, task_id=task_id)
            return _ok(
                req_id,
                {"configs": [c.model_dump(by_alias=True, exclude_none=True) for c in cfgs]},
            )

        if method == "tasks/pushNotificationConfig/get":
            task_id = params.get("taskId")
            cfg_id = params.get("configId") or params.get("id")
            if not isinstance(task_id, str) or not isinstance(cfg_id, str):
                raise A2AError("InvalidParamsError", "taskId + configId required")
            cfgs = await list_push_configs(store=store, task_id=task_id)
            for c in cfgs:
                if c.id == cfg_id:
                    return _ok(req_id, c.model_dump(by_alias=True, exclude_none=True))
            raise A2AError("TaskNotFoundError", f"push config {cfg_id} not found")

        if method == "tasks/pushNotificationConfig/delete":
            task_id = params.get("taskId")
            cfg_id = params.get("configId") or params.get("id")
            if not isinstance(task_id, str) or not isinstance(cfg_id, str):
                raise A2AError("InvalidParamsError", "taskId + configId required")
            await delete_push_config(store=store, task_id=task_id, cfg_id=cfg_id)
            return _ok(req_id, {"ok": True})

        if method == "agent/getCard":
            # Implementations that wire AgentCard generation pass it in via a
            # store attribute; otherwise we surface a structured error so the
            # caller knows to hit /.well-known/agent-card.json directly.
            card_provider = getattr(store, "agent_card", None)
            if callable(card_provider):
                card = card_provider()
                return _ok(req_id, card.model_dump(by_alias=True, exclude_none=True))
            raise A2AError(
                "UnsupportedOperationError",
                "agent/getCard not wired; fetch /.well-known/agent-card.json instead",
            )

        raise A2AError("MethodNotFoundError", f"unknown method: {method}")
    except A2AError as exc:
        return _err_from(req_id, exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("jsonrpc dispatch crashed for method=%s", method)
        return _err(req_id, A2A_ERROR_CODES["InternalError"], str(exc))


async def _stream_method(
    *,
    method: str,
    params: dict[str, Any],
    req_id: Any,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    depth: int,
):
    """Return an async iterator yielding SSE-formatted JSON-RPC events."""
    if method == "message/stream":
        msg_raw = params.get("message")
        if not isinstance(msg_raw, dict):
            raise A2AError("InvalidParamsError", "message: object required")
        message = Message.model_validate(msg_raw)
        events = await stream_message(
            store=store,
            runner=runner,
            message=message,
            context_id=params.get("contextId"),
            depth=depth,
        )
    elif method == "tasks/resubscribe":
        task_id = params.get("id") or params.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            raise A2AError("InvalidParamsError", "id: string required")
        events = await subscribe_task(store=store, task_id=task_id)
    else:  # pragma: no cover - should be filtered upstream
        raise A2AError("MethodNotFoundError", f"unknown streaming method: {method}")

    async def _gen():
        try:
            async for evt in events:
                frame = _ok(req_id, evt)
                yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
        except A2AError as exc:
            yield f"data: {json.dumps(_err_from(req_id, exc), ensure_ascii=False)}\n\n"
        except Exception as exc:  # pragma: no cover
            logger.exception("jsonrpc streaming crashed for method=%s", method)
            yield (
                "data: "
                + json.dumps(_err(req_id, A2A_ERROR_CODES["InternalError"], str(exc)), ensure_ascii=False)
                + "\n\n"
            )

    return _gen()


# ── FastAPI router factory ──────────────────────────────────────────────────
def build_jsonrpc_router(
    *,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    bearer_dep,
    depth_dep,
    path: str = "/a2a/jsonrpc",
) -> APIRouter:
    """Wire the JSON-RPC endpoint into an adapter app.

    ``bearer_dep`` and ``depth_dep`` are the same dependencies used by the
    legacy ``/v1/run`` route in :mod:`agent_mesh_adapter`, so auth and the
    cycle guard behave identically across bindings.
    """
    router = APIRouter()

    @router.post(path, dependencies=[Depends(bearer_dep)])
    async def jsonrpc_endpoint(request: Request, depth: int = Depends(depth_dep)):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                _err(None, A2A_ERROR_CODES["InvalidRequestError"], "invalid JSON"),
                status_code=200,
            )

        # Batch ─ spec Section 9.5 — but batches cannot contain streaming
        # methods; the spec mandates the server reject them as a single
        # InvalidRequestError.
        if isinstance(body, list):
            if any(
                isinstance(req, dict) and req.get("method") in STREAMING_METHODS
                for req in body
            ):
                return JSONResponse(
                    _err(
                        None,
                        A2A_ERROR_CODES["InvalidRequestError"],
                        "streaming methods (message/stream, tasks/resubscribe) are not allowed in batches",
                    ),
                    status_code=200,
                )
            replies: list[dict[str, Any]] = []
            for req in body:
                reply = await _handle_single(req, store, runner, depth)
                if reply is not None:
                    replies.append(reply)
            if not replies:  # All notifications → spec says no response body.
                return JSONResponse(content=None, status_code=204)
            return JSONResponse(replies)

        if not isinstance(body, dict):
            return JSONResponse(
                _err(
                    None,
                    A2A_ERROR_CODES["InvalidRequestError"],
                    "JSON-RPC payload must be an object or an array",
                ),
                status_code=200,
            )

        method = body.get("method")
        if isinstance(method, str) and method in STREAMING_METHODS:
            req_id = body.get("id")
            params = body.get("params") or {}
            if not isinstance(params, dict):
                return JSONResponse(
                    _err(req_id, A2A_ERROR_CODES["InvalidParamsError"], "params must be an object"),
                    status_code=200,
                )
            try:
                event_iter = await _stream_method(
                    method=method,
                    params=params,
                    req_id=req_id,
                    store=store,
                    runner=runner,
                    depth=depth,
                )
            except A2AError as exc:
                return JSONResponse(_err_from(req_id, exc), status_code=200)
            return StreamingResponse(
                event_iter,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store"},
            )

        reply = await _handle_single(body, store, runner, depth)
        if reply is None:
            return JSONResponse(content=None, status_code=204)
        return JSONResponse(reply)

    return router


async def _handle_single(
    req: Any,
    store: InMemoryTaskStore,
    runner: RunnerLike,
    depth: int,
) -> dict[str, Any] | None:
    if not isinstance(req, dict):
        return _err(None, A2A_ERROR_CODES["InvalidRequestError"], "request must be an object")
    if req.get("jsonrpc") != "2.0":
        return _err(req.get("id"), A2A_ERROR_CODES["InvalidRequestError"], "jsonrpc: '2.0' required")
    method = req.get("method")
    if not isinstance(method, str):
        return _err(req.get("id"), A2A_ERROR_CODES["InvalidRequestError"], "method: string required")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return _err(req.get("id"), A2A_ERROR_CODES["InvalidParamsError"], "params must be an object")
    if method in STREAMING_METHODS:
        return _err(
            req.get("id"),
            A2A_ERROR_CODES["InvalidRequestError"],
            f"{method} requires SSE; send as standalone request, not in batch",
        )
    reply = await _dispatch_unary(
        method=method,
        params=params,
        req_id=req.get("id"),
        store=store,
        runner=runner,
        depth=depth,
    )
    # Notifications (request without `id`) get no reply (Section 9.5).
    return None if "id" not in req else reply


__all__ = ["build_jsonrpc_router", "STREAMING_METHODS"]
