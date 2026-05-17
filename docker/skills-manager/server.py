#!/usr/bin/env python3
"""skills-manager — CRUD over the shared ``.ai/skills/`` tree.

The ``.ai/`` directory is bind-mounted **read-only** into every adapter
container; only this service has it ``rw``. Every write fans out a
``POST /admin/reload`` to each registered adapter so their Agent Cards
bump their ``version`` (sha256 of all skill manifests) and downstream
ETag caches invalidate (Section 8.6.1 of the A2A spec).

OpenWebUI registers this service as an OpenAPI tool server on
``http://skills-manager:8000`` with ``Authorization: Bearer
SKILLS_MANAGER_API_KEY``.

Endpoints
---------

- ``GET  /skills`` — list parsed frontmatter for every skill.
- ``GET  /skills/{id}`` — raw ``SKILL.md`` body + parsed metadata.
- ``POST /skills`` — create a new skill from a JSON payload that pairs
  YAML frontmatter fields with a markdown body.
- ``PUT  /skills/{id}`` — replace an existing skill.
- ``DELETE /skills/{id}`` — remove a skill.
- ``POST /skills/import`` — import from ``aitmpl`` / ``claude-templates``
  (uses the ``claude-code-templates`` CLI if installed) or from a raw
  ``url``.
- ``POST /skills/{id}/attach`` — idempotently toggle the ``agents:``
  list inside the frontmatter (turn skill on/off per agent).
- ``POST /admin/reload`` — broadcast a reload to every registered
  adapter. Useful for manual recovery; the CRUD endpoints already do
  it implicitly.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParam
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

# Make the shared loader importable; in the Docker image we copy it next to
# server.py, in dev shells we may run with PYTHONPATH=/repo/docker/agent-mesh-common.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import skills_loader  # type: ignore
except ImportError:
    sys.path.insert(0, "/app")
    import skills_loader  # type: ignore

logger = logging.getLogger("skills-manager")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())


# ── config ───────────────────────────────────────────────────────────────────
SKILLS_DIR = Path(
    os.environ.get("SKILLS_DIR")
    or os.environ.get("AGENT_MESH_SKILLS_DIR")
    or "/workspace/.ai/skills"
)
MANAGER_API_KEY = os.environ.get("SKILLS_MANAGER_API_KEY", "").strip()
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _parse_adapters_env(raw: str) -> list[tuple[str, str, str]]:
    """Parse ``id=base-url`` pairs and look up per-id bearer tokens.

    Returns ``[(id, base_url, bearer), ...]``. Bearer comes from
    ``<UPPERCASE_ID>_ADAPTER_API_KEY``.
    """
    out: list[tuple[str, str, str]] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            continue
        agent_id, base = token.split("=", 1)
        agent_id = agent_id.strip()
        base = base.strip().rstrip("/")
        if not agent_id or not base:
            continue
        env_key = f"{agent_id.upper().replace('-', '_')}_ADAPTER_API_KEY"
        bearer = os.environ.get(env_key, "").strip()
        out.append((agent_id, base, bearer))
    return out


ADAPTERS = _parse_adapters_env(os.environ.get("SKILLS_ADAPTERS", ""))


# ── auth ─────────────────────────────────────────────────────────────────────
def _bearer_dep(authorization: str | None = Header(default=None)) -> None:
    if not MANAGER_API_KEY:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    if authorization.split(" ", 1)[1].strip() != MANAGER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


# ── slug + path safety ───────────────────────────────────────────────────────
def _validate_slug(skill_id: str) -> str:
    skill_id = (skill_id or "").strip().lower()
    if not SLUG_RE.match(skill_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "skill id must match /^[a-z0-9][a-z0-9_-]{0,63}$/ — got "
                f"{skill_id!r}"
            ),
        )
    return skill_id


def _skill_dir(skill_id: str) -> Path:
    skill_id = _validate_slug(skill_id)
    target = (SKILLS_DIR / skill_id).resolve()
    skills_root = SKILLS_DIR.resolve()
    if skills_root not in target.parents and target != skills_root:
        raise HTTPException(status_code=400, detail="path traversal denied")
    return target


def _skill_file(skill_id: str) -> Path:
    return _skill_dir(skill_id) / "SKILL.md"


# ── frontmatter helpers ──────────────────────────────────────────────────────
def _emit_yaml_value(v: Any) -> str:
    if isinstance(v, list):
        items = ", ".join(_emit_yaml_scalar(x) for x in v)
        return f"[{items}]"
    return _emit_yaml_scalar(v)


def _emit_yaml_scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if not s:
        return '""'
    if any(ch in s for ch in ":#\n[]{},&*?|<>=!%@`") or s.lstrip()[:1] in {"-", "?", ":"}:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


_FRONTMATTER_KEYS = (
    "id",
    "name",
    "description",
    "version",
    "tags",
    "agents",
    "triggers",
    "inputModes",
    "outputModes",
    "mcp_servers",
    "examples",
    "securityRequirements",
)


def _build_skill_md(meta: dict[str, Any], body: str) -> str:
    """Serialize a SKILL.md with a stable key order.

    Unknown keys are preserved at the end of the frontmatter so callers
    can extend the schema without losing fields on round-trip.
    """
    buf = io.StringIO()
    buf.write("---\n")
    seen: set[str] = set()
    for key in _FRONTMATTER_KEYS:
        if key not in meta:
            continue
        seen.add(key)
        value = meta[key]
        if isinstance(value, list):
            if not value:
                buf.write(f"{key}: []\n")
                continue
            scalars = all(not isinstance(x, (dict, list)) for x in value)
            if scalars and len(value) <= 6 and sum(len(str(x)) for x in value) < 80:
                buf.write(f"{key}: {_emit_yaml_value(value)}\n")
            else:
                buf.write(f"{key}:\n")
                for item in value:
                    buf.write(f"  - {_emit_yaml_scalar(item)}\n")
        else:
            buf.write(f"{key}: {_emit_yaml_value(value)}\n")
    for key, value in meta.items():
        if key in seen:
            continue
        if isinstance(value, list):
            buf.write(f"{key}: {_emit_yaml_value(value)}\n")
        else:
            buf.write(f"{key}: {_emit_yaml_value(value)}\n")
    buf.write("---\n\n")
    buf.write(body.lstrip("\n"))
    if not body.endswith("\n"):
        buf.write("\n")
    return buf.getvalue()


def _load_skill_record(skill_id: str) -> dict[str, Any]:
    path = _skill_file(skill_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"skill '{skill_id}' not found")
    skill = skills_loader._skill_from_path(path)
    if skill is None:
        raise HTTPException(status_code=500, detail=f"failed to parse {path}")
    return _skill_to_dict(skill)


def _skill_to_dict(skill: skills_loader.Skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "tags": skill.tags,
        "agents": skill.agents,
        "triggers": skill.triggers,
        "inputModes": skill.input_modes,
        "outputModes": skill.output_modes,
        "mcp_servers": skill.mcp_servers,
        "examples": skill.examples,
        "securityRequirements": skill.security_requirements,
        "body": skill.body,
        "extra": skill.extra,
        "path": skill.path,
    }


# ── reload broadcast ─────────────────────────────────────────────────────────
async def _broadcast_reload() -> dict[str, Any]:
    if not ADAPTERS:
        return {"adapters": [], "ok": True}
    results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def _one(agent_id: str, base: str, bearer: str) -> dict[str, Any]:
            url = f"{base}/admin/reload"
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {bearer}"} if bearer else {},
                )
                payload: Any
                try:
                    payload = resp.json()
                except ValueError:
                    payload = resp.text
                return {
                    "agent_id": agent_id,
                    "url": url,
                    "status": resp.status_code,
                    "ok": resp.is_success,
                    "body": payload,
                }
            except httpx.HTTPError as exc:
                return {
                    "agent_id": agent_id,
                    "url": url,
                    "status": 0,
                    "ok": False,
                    "error": str(exc),
                }

        coros = [_one(a, b, k) for (a, b, k) in ADAPTERS]
        results = await asyncio.gather(*coros)
    overall = all(r["ok"] for r in results) if results else True
    return {"ok": overall, "adapters": results}


# ── HTTP surface ─────────────────────────────────────────────────────────────
class SkillCreate(BaseModel):
    """Payload for ``POST /skills`` and ``PUT /skills/{id}``."""

    id: str = Field(..., description="Slug — same as the directory name.")
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    version: str = Field(default="0.1.0")
    tags: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=lambda: ["*"])
    triggers: list[str] = Field(default_factory=list)
    inputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    outputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    mcp_servers: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    securityRequirements: list[Any] = Field(default_factory=list)
    body: str = Field(default="")
    extra: dict[str, Any] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    source: str = Field(
        ...,
        description=(
            "Where to fetch from: `aitmpl` / `claude-templates` (CLI), "
            "or `url` (raw markdown URL)."
        ),
    )
    ref: str = Field(..., description="Catalog ref (`development/webapp-testing`) or URL.")
    rename_to: str | None = Field(
        default=None,
        description="Optional slug to rename the imported skill to.",
    )


class AttachRequest(BaseModel):
    agent_id: str = Field(..., description="Adapter id to toggle, e.g. `clawcode`.")
    enabled: bool = Field(default=True)


app = FastAPI(
    title="Skills Manager",
    version="0.1.0",
    description=(
        "CRUD over the shared `.ai/skills/` tree. Every write fans out a "
        "`/admin/reload` to each adapter so Agent Card versions bump and "
        "ETag caches invalidate."
    ),
)


@app.on_event("startup")
async def _startup() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "skills-manager up; dir=%s adapters=%s auth=%s",
        SKILLS_DIR,
        ",".join(a[0] for a in ADAPTERS),
        "on" if MANAGER_API_KEY else "OFF",
    )


@app.middleware("http")
async def _bearer_on_openapi(request, call_next):
    if request.url.path == "/openapi.json" and MANAGER_API_KEY:
        try:
            _bearer_dep(request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "skills_dir": str(SKILLS_DIR),
        "exists": SKILLS_DIR.is_dir(),
        "adapters": [a[0] for a in ADAPTERS],
    }


@app.get(
    "/skills",
    summary="List every skill in the catalogue.",
    dependencies=[Depends(_bearer_dep)],
)
async def list_skills() -> dict[str, Any]:
    skills = skills_loader.load_skills(SKILLS_DIR, agent_id="*")
    return {
        "count": len(skills),
        "version": skills_loader.skills_version(skills),
        "skills": [_skill_to_dict(s) for s in skills],
    }


@app.get(
    "/skills/{skill_id}",
    summary="Read one skill (parsed metadata + body).",
    dependencies=[Depends(_bearer_dep)],
)
async def get_skill(skill_id: str = PathParam(...)) -> dict[str, Any]:
    return _load_skill_record(skill_id)


@app.get(
    "/skills/{skill_id}/raw",
    summary="Read the raw SKILL.md bytes (frontmatter + body).",
    dependencies=[Depends(_bearer_dep)],
    response_class=PlainTextResponse,
)
async def get_skill_raw(skill_id: str = PathParam(...)) -> str:
    path = _skill_file(skill_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return path.read_text(encoding="utf-8")


@app.post(
    "/skills",
    summary="Create a new skill.",
    status_code=201,
    dependencies=[Depends(_bearer_dep)],
)
async def create_skill(payload: SkillCreate) -> dict[str, Any]:
    skill_id = _validate_slug(payload.id)
    target_dir = _skill_dir(skill_id)
    if target_dir.exists():
        raise HTTPException(status_code=409, detail=f"skill '{skill_id}' exists")
    return await _write_skill(skill_id, payload, overwrite=False)


@app.put(
    "/skills/{skill_id}",
    summary="Replace an existing skill (full upsert).",
    dependencies=[Depends(_bearer_dep)],
)
async def replace_skill(skill_id: str, payload: SkillCreate) -> dict[str, Any]:
    if payload.id and payload.id != skill_id:
        raise HTTPException(
            status_code=400,
            detail=f"payload.id={payload.id!r} does not match path {skill_id!r}",
        )
    payload = payload.model_copy(update={"id": skill_id})
    return await _write_skill(skill_id, payload, overwrite=True)


@app.delete(
    "/skills/{skill_id}",
    summary="Remove a skill from disk.",
    dependencies=[Depends(_bearer_dep)],
)
async def delete_skill(skill_id: str) -> dict[str, Any]:
    target_dir = _skill_dir(skill_id)
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"skill '{skill_id}' not found")
    shutil.rmtree(target_dir)
    reload_status = await _broadcast_reload()
    return {"ok": True, "id": skill_id, "reload": reload_status}


@app.post(
    "/skills/{skill_id}/attach",
    summary="Toggle the skill on/off for one adapter (idempotent).",
    dependencies=[Depends(_bearer_dep)],
)
async def attach_skill(skill_id: str, payload: AttachRequest) -> dict[str, Any]:
    record = _load_skill_record(skill_id)
    agents = list(record.get("agents") or [])
    target = payload.agent_id.strip()
    if not target:
        raise HTTPException(status_code=400, detail="agent_id is required")
    star = "*" in agents
    if payload.enabled:
        if star or target in agents:
            return {"ok": True, "id": skill_id, "agents": agents, "noop": True}
        agents.append(target)
    else:
        if star:
            # Materialise the wildcard to the explicit list so we can drop one.
            agents = [a for a in ADAPTERS_IDS if a != target]
        else:
            agents = [a for a in agents if a != target]
        if not agents:
            agents = []
    create_payload = SkillCreate(
        id=skill_id,
        name=record["name"],
        description=record["description"],
        version=record["version"],
        tags=record["tags"],
        agents=agents,
        triggers=record["triggers"],
        inputModes=record["inputModes"],
        outputModes=record["outputModes"],
        mcp_servers=record["mcp_servers"],
        examples=record["examples"],
        securityRequirements=record["securityRequirements"],
        body=record["body"],
        extra=record.get("extra") or {},
    )
    return await _write_skill(skill_id, create_payload, overwrite=True)


@app.post(
    "/skills/import",
    summary="Import a skill from aitmpl / claude-templates / a URL.",
    dependencies=[Depends(_bearer_dep)],
)
async def import_skill(payload: ImportRequest) -> dict[str, Any]:
    src = payload.source.strip().lower()
    if src == "url":
        skill_id = _import_from_url(payload.ref, payload.rename_to)
    elif src in {"aitmpl", "claude-templates", "claude-code-templates"}:
        skill_id = _import_from_cli(payload.ref, payload.rename_to)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"unknown source {src!r}; expected url|aitmpl|claude-templates",
        )
    reload_status = await _broadcast_reload()
    return {"ok": True, "id": skill_id, "reload": reload_status}


@app.post(
    "/admin/reload",
    summary="Force-reload skills on every adapter.",
    dependencies=[Depends(_bearer_dep)],
)
async def admin_reload() -> dict[str, Any]:
    return await _broadcast_reload()


@app.get("/", include_in_schema=False)
async def root() -> dict[str, Any]:
    return {
        "service": "skills-manager",
        "skills_dir": str(SKILLS_DIR),
        "adapters": [a[0] for a in ADAPTERS],
        "openapi": "/openapi.json",
    }


# ── write helpers ────────────────────────────────────────────────────────────
ADAPTERS_IDS = [a[0] for a in ADAPTERS]


async def _write_skill(
    skill_id: str,
    payload: SkillCreate,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    target_dir = _skill_dir(skill_id)
    if target_dir.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"skill '{skill_id}' exists")
    target_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "id": skill_id,
        "name": payload.name,
        "description": payload.description,
        "version": payload.version,
        "tags": payload.tags,
        "agents": payload.agents,
        "triggers": payload.triggers,
        "inputModes": payload.inputModes,
        "outputModes": payload.outputModes,
        "mcp_servers": payload.mcp_servers,
        "examples": payload.examples,
        "securityRequirements": payload.securityRequirements,
    }
    for k, v in (payload.extra or {}).items():
        if k not in meta:
            meta[k] = v
    text = _build_skill_md(meta, payload.body or "")
    target_file = target_dir / "SKILL.md"
    target_file.write_text(text, encoding="utf-8")
    record = _load_skill_record(skill_id)
    reload_status = await _broadcast_reload()
    return {
        "ok": True,
        "id": skill_id,
        "skill": record,
        "reload": reload_status,
    }


# ── importers ────────────────────────────────────────────────────────────────
def _import_from_url(url: str, rename_to: str | None) -> str:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="ref must be http(s) URL")
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}")
    body = resp.text
    return _store_imported_markdown(body, rename_to=rename_to)


def _import_from_cli(ref: str, rename_to: str | None) -> str:
    """Use the ``claude-code-templates`` CLI if available.

    The CLI lives on npm (``npm i -g claude-code-templates``) and copies
    a skill subtree to a target dir. We point it at a temp dir, then
    move the result into ``SKILLS_DIR``.
    """
    cli = shutil.which("claude-code-templates") or shutil.which("npx")
    if not cli:
        raise HTTPException(
            status_code=501,
            detail=(
                "neither `claude-code-templates` nor `npx` is installed in "
                "the skills-manager image. Install it (npm i -g "
                "claude-code-templates) or use source=`url` instead."
            ),
        )
    with tempfile.TemporaryDirectory(prefix="skills-import-") as tmp:
        cmd: list[str]
        if cli.endswith("npx"):
            cmd = [cli, "--yes", "claude-code-templates", "--skill", ref, "--out", tmp]
        else:
            cmd = [cli, "--skill", ref, "--out", tmp]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="import timed out (120s)")
        if proc.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=(
                    "claude-code-templates failed: "
                    f"{(proc.stderr or proc.stdout).strip()[:1024]}"
                ),
            )
        candidates = [
            p
            for p in Path(tmp).rglob("SKILL.md")
            if p.is_file()
        ]
        if not candidates:
            raise HTTPException(
                status_code=502,
                detail="import succeeded but produced no SKILL.md — check ref",
            )
        primary = sorted(candidates, key=lambda p: len(p.parts))[0]
        return _store_imported_markdown(
            primary.read_text(encoding="utf-8"),
            rename_to=rename_to or primary.parent.name,
        )


def _store_imported_markdown(text: str, rename_to: str | None) -> str:
    """Persist an imported SKILL.md, deriving the slug from frontmatter."""
    fm, body = skills_loader._split_frontmatter(text)
    meta = skills_loader._parse_yaml(fm)
    raw_id = (rename_to or meta.get("id") or "").strip()
    if not raw_id:
        raise HTTPException(
            status_code=400,
            detail="cannot determine skill id (no `id:` and no rename_to)",
        )
    skill_id = _validate_slug(raw_id)
    target_dir = _skill_dir(skill_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    if rename_to:
        meta["id"] = skill_id
        text = _build_skill_md(meta, body)
    (target_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_id


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
        port=int(os.environ.get("LISTEN_PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
