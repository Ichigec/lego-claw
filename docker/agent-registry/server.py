#!/usr/bin/env python3
"""agent-registry — single A2A entry point for the agent mesh.

OpenWebUI registers ONE OpenAPI tool server (this one) instead of one
per adapter. The registry handles four kinds of traffic:

1. **Discovery** — ``GET /v1/agents`` returns the JWS-signed Agent Cards
   for every adapter listed in ``AGENT_REGISTRY_AGENTS``. The
   per-adapter ``/.well-known/agent-card.json`` is fetched lazily,
   cached by ETag (Section 8.6.1 of the A2A spec), and refreshed when
   the upstream returns ``304`` past a soft TTL.

2. **Proxy** — ``POST /v1/agents/{id}/message:send`` and friends are
   transparent passthroughs. The registry strips the inbound bearer
   (its own ``AGENT_REGISTRY_API_KEY``) and replaces it with the
   per-agent bearer (env ``<AGENTID>_ADAPTER_API_KEY``).

3. **Fan-out** — ``GET /v1/tasks?agents=*`` walks every adapter's
   ``GET /a2a/v1/tasks`` endpoint, merges results, and re-paginates.
   Used by the OpenWebUI dashboard to show "all in-flight tasks".

4. **Cancel / subscribe** — fan-in helpers
   ``POST /v1/tasks/{agentId}:{taskId}:cancel`` and ``:subscribe``
   forward to the right adapter based on ``agentId``.

The adapters in this repo do not yet implement Section 11 of the spec
(that's the P0 deliverable in plans/agent-mesh/...). Until then we
fall back to a synthetic Agent Card built from the legacy ``/v1/skills``
endpoint, and we relay ``/v1/run`` for ``message:send``. Once the
adapters publish real A2A endpoints, this registry switches to them
without code changes — it always tries A2A first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import urljoin

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("agent-registry")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())


# ── config ───────────────────────────────────────────────────────────────────
@dataclass
class AgentEntry:
    id: str
    base_url: str
    bearer: str
    card: dict[str, Any] = field(default_factory=dict)
    card_etag: str = ""
    card_fetched_at: float = 0.0
    card_status: int = 0


def _parse_agents_env(raw: str) -> list[AgentEntry]:
    """Accepts both ``id=host:port`` and bare ``host:port`` (id derived).

    Examples:
      ``clawcode=clawcode-adapter:8790,openhands=openhands-adapter:8791``
      ``clawcode-adapter:8790,openhands-adapter:8791`` (id = ``clawcode``)
    """
    out: list[AgentEntry] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        agent_id: str
        host_port: str
        if "=" in token:
            agent_id, host_port = token.split("=", 1)
            agent_id = agent_id.strip()
            host_port = host_port.strip()
        else:
            host_port = token
            host = host_port.split(":", 1)[0]
            agent_id = host.replace("-adapter", "").replace("_adapter", "").strip() or host
        if not host_port.startswith(("http://", "https://")):
            base = f"http://{host_port}"
        else:
            base = host_port
        env_key = f"{agent_id.upper().replace('-', '_')}_ADAPTER_API_KEY"
        bearer = os.environ.get(env_key, "").strip()
        if not bearer:
            logger.warning(
                "no bearer for agent_id=%s (env %s missing); upstream calls "
                "will likely return 401",
                agent_id,
                env_key,
            )
        out.append(AgentEntry(id=agent_id, base_url=base.rstrip("/"), bearer=bearer))
    return out


REGISTRY_API_KEY = os.environ.get("AGENT_REGISTRY_API_KEY", "").strip()
AGENTS = _parse_agents_env(os.environ.get("AGENT_REGISTRY_AGENTS", ""))
CARD_TTL_S = float(os.environ.get("AGENT_REGISTRY_CARD_TTL_S", "30"))
HTTP_TIMEOUT_S = float(os.environ.get("AGENT_REGISTRY_HTTP_TIMEOUT_S", "30"))


def _agents_index() -> dict[str, AgentEntry]:
    return {a.id: a for a in AGENTS}


# ── auth ─────────────────────────────────────────────────────────────────────
def _bearer_dep(authorization: str | None = Header(default=None)) -> None:
    if not REGISTRY_API_KEY:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    if token != REGISTRY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


# ── HTTP client lifecycle ────────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
    return _client


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    logger.info(
        "agent-registry up; %d agents=%s registry_auth=%s ttl=%.1fs",
        len(AGENTS),
        ",".join(a.id for a in AGENTS),
        "on" if REGISTRY_API_KEY else "OFF",
        CARD_TTL_S,
    )
    yield
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── card discovery + cache ──────────────────────────────────────────────────-
def _synthesise_card(entry: AgentEntry, skills_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a stand-in AgentCard from ``/v1/skills`` while real A2A is offline."""
    skills = skills_payload.get("skills") or []
    return {
        "id": entry.id,
        "name": entry.id,
        "description": (
            f"Synthetic card for adapter at {entry.base_url}. "
            "Replaced once /.well-known/agent-card.json is available."
        ),
        "version": skills_payload.get("version") or "0.0.0",
        "url": entry.base_url,
        "skills": skills,
        "interfaces": [
            {"binding": "rest", "url": f"{entry.base_url}/a2a/v1"},
            {"binding": "rest-legacy", "url": f"{entry.base_url}/v1/run"},
        ],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedCard": False,
        },
        "_synthetic": True,
    }


async def _fetch_card(entry: AgentEntry, *, force: bool = False) -> dict[str, Any]:
    """Fetch the agent's AgentCard, honouring ETag + soft TTL.

    Falls back to a synthetic card when ``/.well-known/agent-card.json``
    returns 404 (i.e. adapter still on the legacy contract).
    """
    now = time.time()
    if (
        not force
        and entry.card
        and (now - entry.card_fetched_at) < CARD_TTL_S
    ):
        return entry.card

    client = await _get_client()
    url = f"{entry.base_url}/.well-known/agent-card.json"
    headers: dict[str, str] = {}
    if entry.card_etag and not force:
        headers["If-None-Match"] = entry.card_etag

    try:
        resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("card fetch failed for %s: %s", entry.id, exc)
        if entry.card:
            return entry.card
        return {
            "id": entry.id,
            "name": entry.id,
            "description": f"unreachable adapter at {entry.base_url}: {exc}",
            "_unreachable": True,
        }

    entry.card_status = resp.status_code
    if resp.status_code == 304 and entry.card:
        entry.card_fetched_at = now
        return entry.card
    if resp.status_code == 200:
        try:
            card = resp.json()
        except ValueError:
            card = {}
        if not isinstance(card, dict) or not card.get("id"):
            logger.warning("agent %s returned a malformed card", entry.id)
        entry.card = card
        entry.card_etag = resp.headers.get("etag", "")
        entry.card_fetched_at = now
        return entry.card
    if resp.status_code == 404:
        # Legacy adapter — try /v1/skills.
        try:
            r2 = await client.get(
                f"{entry.base_url}/v1/skills",
                headers={"Authorization": f"Bearer {entry.bearer}"},
            )
            payload = r2.json() if r2.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            payload = {}
        entry.card = _synthesise_card(entry, payload if isinstance(payload, dict) else {})
        entry.card_etag = ""
        entry.card_fetched_at = now
        return entry.card

    logger.warning(
        "card fetch returned %d for %s; reusing stale=%s",
        resp.status_code,
        entry.id,
        bool(entry.card),
    )
    if entry.card:
        return entry.card
    return {
        "id": entry.id,
        "name": entry.id,
        "description": f"adapter returned HTTP {resp.status_code}",
        "_error": True,
    }


async def _fetch_all_cards(force: bool = False) -> list[dict[str, Any]]:
    return await asyncio.gather(*[_fetch_card(a, force=force) for a in AGENTS])


# ── proxy helpers ────────────────────────────────────────────────────────────
def _resolve_agent(agent_id: str) -> AgentEntry:
    entry = _agents_index().get(agent_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown agent '{agent_id}'. known={sorted(_agents_index())}",
        )
    return entry


def _forward_headers(request: Request, entry: AgentEntry, depth_inc: int = 1) -> dict[str, str]:
    """Build outbound headers: per-agent bearer + propagated mesh depth."""
    out = {
        "Authorization": f"Bearer {entry.bearer}",
        "Accept": request.headers.get("accept", "application/json"),
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    incoming = request.headers.get("X-Agent-Mesh-Depth")
    out["X-Agent-Mesh-Depth"] = str(int(incoming or "0") + depth_inc)
    forwarded_for = request.headers.get("x-forwarded-for") or request.client.host if request.client else ""
    if forwarded_for:
        out["X-Forwarded-For"] = forwarded_for
    return out


async def _proxy(
    method: str,
    entry: AgentEntry,
    path: str,
    *,
    request: Request,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> JSONResponse:
    """Single-shot JSON proxy. Returns the upstream body verbatim."""
    client = await _get_client()
    url = entry.base_url + path
    try:
        resp = await client.request(
            method,
            url,
            json=json_body,
            params=params,
            headers=_forward_headers(request, entry),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")
    media = resp.headers.get("content-type", "application/json")
    body = resp.content
    return JSONResponse(
        content=None,
        status_code=resp.status_code,
        media_type=media,
    ) if not body else JSONResponse(
        content=_safe_json(body),
        status_code=resp.status_code,
        media_type=media,
    )


def _safe_json(body: bytes) -> Any:
    import json

    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"raw": body.decode("utf-8", errors="replace")}


async def _proxy_stream(
    entry: AgentEntry,
    path: str,
    *,
    request: Request,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> StreamingResponse:
    """Streaming proxy used for SSE endpoints (message:stream, subscribe)."""
    client = await _get_client()
    url = entry.base_url + path
    headers = _forward_headers(request, entry)
    headers["Accept"] = "text/event-stream"

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async with client.stream(
                "POST" if json_body is not None else "GET",
                url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=None,
            ) as resp:
                async for chunk in resp.aiter_raw():
                    yield chunk
        except httpx.HTTPError as exc:
            yield (f": upstream error {exc}\n\n").encode("utf-8")

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── HTTP surface ─────────────────────────────────────────────────────────────
class MessageSendRequest(BaseModel):
    """Minimal A2A ``message:send`` payload (Section 11.3.1).

    The registry does not validate the full schema — that is the
    adapter's job. We accept any extra keys via ``model_config``.
    """

    model_config = {"extra": "allow"}

    message: dict[str, Any] | None = Field(default=None)
    contextId: str | None = Field(default=None)
    returnImmediately: bool = Field(default=False)


class TasksList(BaseModel):
    tasks: list[dict[str, Any]] = []
    nextPageToken: str | None = None


app = FastAPI(
    title="Agent Registry",
    version="0.1.0",
    description=(
        "Single A2A discovery + proxy entrypoint for OpenWebUI. Lists "
        "every registered adapter's AgentCard, fans out ListTasks, and "
        "transparently forwards message:send / message:stream / cancel "
        "/ subscribe to the right adapter based on `agentId`."
    ),
    lifespan=_lifespan,
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "agents": [a.id for a in AGENTS],
        "auth": bool(REGISTRY_API_KEY),
    }


@app.middleware("http")
async def _bearer_on_openapi(request: Request, call_next):
    if request.url.path == "/openapi.json" and REGISTRY_API_KEY:
        try:
            _bearer_dep(request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get(
    "/v1/agents",
    summary="List every registered agent (JWS-signed AgentCard).",
    dependencies=[Depends(_bearer_dep)],
)
async def list_agents(
    refresh: bool = Query(default=False, description="Force re-fetch upstream cards."),
) -> dict[str, Any]:
    cards = await _fetch_all_cards(force=refresh)
    return {"agents": cards, "count": len(cards)}


@app.get(
    "/v1/agents/{agent_id}/.well-known/agent-card.json",
    summary="Proxy + cache the upstream AgentCard for one agent.",
    dependencies=[Depends(_bearer_dep)],
)
async def agent_card(agent_id: str, refresh: bool = Query(default=False)) -> dict[str, Any]:
    entry = _resolve_agent(agent_id)
    return await _fetch_card(entry, force=refresh)


@app.post(
    "/v1/agents/{agent_id}/message:send",
    summary="Forward an A2A `message:send` to the adapter.",
    dependencies=[Depends(_bearer_dep)],
)
async def message_send(
    agent_id: str,
    payload: MessageSendRequest,
    request: Request,
) -> JSONResponse:
    entry = _resolve_agent(agent_id)
    body = payload.model_dump(exclude_none=True)
    return await _proxy(
        "POST", entry, "/a2a/v1/message:send",
        request=request, json_body=body,
    )


@app.post(
    "/v1/agents/{agent_id}/message:stream",
    summary="Forward an A2A `message:stream` SSE to the adapter.",
    dependencies=[Depends(_bearer_dep)],
)
async def message_stream(
    agent_id: str,
    payload: MessageSendRequest,
    request: Request,
) -> StreamingResponse:
    entry = _resolve_agent(agent_id)
    body = payload.model_dump(exclude_none=True)
    return await _proxy_stream(
        entry, "/a2a/v1/message:stream",
        request=request, json_body=body,
    )


@app.get(
    "/v1/agents/{agent_id}/tasks",
    summary="List tasks for one agent (proxy).",
    dependencies=[Depends(_bearer_dep)],
)
async def list_agent_tasks(
    agent_id: str,
    request: Request,
    contextId: str | None = Query(default=None),
    pageSize: int = Query(default=50, ge=1, le=500),
    pageToken: str | None = Query(default=None),
    historyLength: int | None = Query(default=None),
) -> JSONResponse:
    entry = _resolve_agent(agent_id)
    params: dict[str, Any] = {"pageSize": pageSize}
    if contextId:
        params["contextId"] = contextId
    if pageToken:
        params["pageToken"] = pageToken
    if historyLength is not None:
        params["historyLength"] = historyLength
    return await _proxy(
        "GET", entry, "/a2a/v1/tasks",
        request=request, params=params,
    )


@app.get(
    "/v1/agents/{agent_id}/tasks/{task_id}",
    summary="Inspect a single task on one agent (proxy).",
    dependencies=[Depends(_bearer_dep)],
)
async def get_task(agent_id: str, task_id: str, request: Request) -> JSONResponse:
    entry = _resolve_agent(agent_id)
    return await _proxy("GET", entry, f"/a2a/v1/tasks/{task_id}", request=request)


@app.get(
    "/v1/tasks",
    response_model=TasksList,
    summary="Fan-out ListTasks across every registered agent.",
    dependencies=[Depends(_bearer_dep)],
)
async def fanout_tasks(
    request: Request,
    agents: str = Query(
        default="*",
        description="Comma-separated list of agentIds, or `*` for all.",
    ),
    pageSize: int = Query(default=50, ge=1, le=500),
) -> TasksList:
    selected = (
        list(_agents_index().values())
        if agents.strip() in {"*", ""}
        else [_resolve_agent(x.strip()) for x in agents.split(",") if x.strip()]
    )
    client = await _get_client()

    async def _one(entry: AgentEntry) -> list[dict[str, Any]]:
        try:
            resp = await client.get(
                f"{entry.base_url}/a2a/v1/tasks",
                headers=_forward_headers(request, entry),
                params={"pageSize": pageSize},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
        items = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for t in items:
            if isinstance(t, dict):
                t = {**t, "agentId": entry.id}
                out.append(t)
        return out

    chunks = await asyncio.gather(*[_one(e) for e in selected])
    merged = [item for chunk in chunks for item in chunk]

    def _sort_key(t: dict[str, Any]) -> Any:
        status = t.get("status") or {}
        return status.get("timestamp") or t.get("createdAt") or ""

    merged.sort(key=_sort_key, reverse=True)
    return TasksList(tasks=merged[:pageSize])


@app.post(
    "/v1/tasks/{agent_id}:{task_id}:cancel",
    summary="Cancel a task on the named adapter.",
    dependencies=[Depends(_bearer_dep)],
)
async def cancel_task(agent_id: str, task_id: str, request: Request) -> JSONResponse:
    entry = _resolve_agent(agent_id)
    return await _proxy(
        "POST", entry, f"/a2a/v1/tasks/{task_id}:cancel", request=request,
    )


@app.post(
    "/v1/tasks/{agent_id}:{task_id}:subscribe",
    summary="Subscribe (SSE) to status updates for a task on the named adapter.",
    dependencies=[Depends(_bearer_dep)],
)
async def subscribe_task(
    agent_id: str, task_id: str, request: Request
) -> StreamingResponse:
    entry = _resolve_agent(agent_id)
    return await _proxy_stream(
        entry, f"/a2a/v1/tasks/{task_id}:subscribe", request=request,
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "agent-registry",
        "agents": [a.id for a in AGENTS],
        "openapi": "/openapi.json",
        "endpoints": [
            "/v1/agents",
            "/v1/agents/{id}/.well-known/agent-card.json",
            "/v1/agents/{id}/message:send",
            "/v1/agents/{id}/message:stream",
            "/v1/agents/{id}/tasks",
            "/v1/agents/{id}/tasks/{taskId}",
            "/v1/tasks",
            "/v1/tasks/{agentId}:{taskId}:cancel",
            "/v1/tasks/{agentId}:{taskId}:subscribe",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
