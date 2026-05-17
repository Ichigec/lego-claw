"""Skills loader shared by all agent-mesh adapters.

Reads the on-disk skill catalogue (the ``.ai/`` tree mounted into the
adapter container at ``/workspace/project/.ai`` by default), produces a
list of ``Skill`` records, and offers helpers for the two consumers
that need them:

* the **A2A Agent Card** assembler — each ``Skill`` becomes an
  ``AgentSkill`` entry (Section 4.4.5 of the A2A spec);
* the **Runner** that spawns the underlying CLI — gets a composed
  system prompt and the merged MCP-server list to inject as env.

The on-disk format is the aitmpl.com / Claude Code "skill" convention:
each skill lives at ``skills/<id>/SKILL.md`` and starts with a YAML
frontmatter delimited by ``---``. Top-level files
``AGENTS.md`` (always-on) and ``router.md`` (skill selection guide) are
prepended verbatim to the system prompt. See ``docs/skills.md`` for the
canonical schema.

We deliberately keep the YAML parser permissive: missing keys default
to empty / "*" so a freshly-imported skill from upstream catalogues
boots even if it skips repo-specific fields.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("agent_mesh_skills")


# ── public dataclass ─────────────────────────────────────────────────────────
@dataclass
class Skill:
    """In-memory skill record. ``raw_manifest`` is the bytes used for
    version hashing — keep it stable regardless of presentation order."""

    id: str
    name: str
    description: str
    version: str
    tags: list[str]
    agents: list[str]
    triggers: list[str]
    input_modes: list[str]
    output_modes: list[str]
    mcp_servers: list[str]
    examples: list[str]
    security_requirements: list[Any]
    body: str
    raw_manifest: str
    path: str
    extra: dict[str, Any] = field(default_factory=dict)

    def matches_agent(self, agent_id: str) -> bool:
        """Whether this skill should be auto-attached to ``agent_id``."""
        if not self.agents:
            return True
        for entry in self.agents:
            if entry == "*" or entry == agent_id:
                return True
        return False

    def to_agent_skill(self) -> dict[str, Any]:
        """Project to the A2A ``AgentSkill`` schema (Section 4.4.5).

        Returns a plain dict in the camelCase wire form so this method
        does not pull in :mod:`a2a.types` at import-time. Use
        :meth:`to_a2a_skill` if you want the typed Pydantic model
        (e.g. when feeding :func:`a2a.agent_card.build_agent_card`).
        """
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "inputModes": list(self.input_modes) or ["text/plain"],
            "outputModes": list(self.output_modes) or ["text/plain"],
        }
        if self.examples:
            out["examples"] = list(self.examples)
        if self.security_requirements:
            out["securityRequirements"] = list(self.security_requirements)
        return out

    def to_a2a_skill(self) -> Any:
        """Return the typed :class:`a2a.types.AgentSkill` for this skill.

        Lazy-imports ``a2a.types`` so the loader stays usable when the
        a2a package isn't on the path (the skills-manager service, for
        example, ships without it).
        """
        from a2a.types import AgentSkill  # imported lazily on purpose

        return AgentSkill.model_validate(self.to_agent_skill())


# ── frontmatter parser ───────────────────────────────────────────────────────
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_yaml, body)``. Empty frontmatter on miss."""
    if not text.startswith("---"):
        return "", text
    nl = text.find("\n")
    if nl == -1:
        return "", text
    end = text.find("\n---", nl)
    if end == -1:
        return "", text
    fm = text[nl + 1 : end]
    rest = text[end + len("\n---") :].lstrip("\n")
    return fm, rest


def _parse_yaml(fm: str) -> dict[str, Any]:
    """Parse the small YAML subset we accept in skill frontmatter.

    We only allow:
    * ``key: value`` scalar lines
    * ``key:`` followed by indented ``- item`` list lines
    * ``key: [a, b, c]`` inline lists
    * ``key: |``/``key: >`` block scalars (collapsed to one string)

    PyYAML would be cleaner but we want zero extra dependencies in the
    shared library; the adapter Dockerfiles pin a minimal set.
    """
    if not fm.strip():
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(fm)
        return data if isinstance(data, dict) else {}
    except ImportError:
        pass

    out: dict[str, Any] = {}
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in stripped:
            i += 1
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("|", ">"):
            block: list[str] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                if ln and not ln.startswith((" ", "\t")):
                    break
                block.append(ln.strip())
                i += 1
            out[key] = "\n".join(block).strip()
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
            out[key] = items
            i += 1
            continue
        if value == "":
            items: list[Any] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                if not ln.startswith((" ", "\t")):
                    break
                ln_str = ln.strip()
                if ln_str.startswith("- "):
                    items.append(ln_str[2:].strip().strip('"').strip("'"))
                elif ln_str.startswith("-"):
                    items.append(ln_str[1:].strip().strip('"').strip("'"))
                else:
                    break
                i += 1
            out[key] = items
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
        i += 1
    return out


# ── loader ───────────────────────────────────────────────────────────────────
def _coerce_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v]
    return [v]


def _coerce_str_list(v: Any) -> list[str]:
    return [str(x) for x in _coerce_list(v) if str(x).strip()]


def _skill_from_path(skill_path: Path) -> Skill | None:
    try:
        text = _read_text(skill_path)
    except OSError as exc:
        logger.warning("cannot read %s: %s", skill_path, exc)
        return None
    fm, body = _split_frontmatter(text)
    meta = _parse_yaml(fm)
    if not meta:
        logger.warning("%s has empty frontmatter; skipping", skill_path)
        return None
    skill_id = str(meta.get("id") or skill_path.parent.name).strip()
    if not skill_id:
        logger.warning("%s has no id; skipping", skill_path)
        return None
    return Skill(
        id=skill_id,
        name=str(meta.get("name") or skill_id),
        description=str(meta.get("description") or "").strip(),
        version=str(meta.get("version") or "0.1.0"),
        tags=_coerce_str_list(meta.get("tags")),
        agents=_coerce_str_list(meta.get("agents")) or ["*"],
        triggers=_coerce_str_list(meta.get("triggers")),
        input_modes=_coerce_str_list(meta.get("inputModes")) or ["text/plain"],
        output_modes=_coerce_str_list(meta.get("outputModes")) or ["text/plain"],
        mcp_servers=_coerce_str_list(meta.get("mcp_servers")),
        examples=_coerce_str_list(meta.get("examples")),
        security_requirements=_coerce_list(meta.get("securityRequirements")),
        body=body,
        raw_manifest=text,
        path=str(skill_path),
        extra={k: v for k, v in meta.items() if k not in {
            "id", "name", "description", "version", "tags", "agents", "triggers",
            "inputModes", "outputModes", "mcp_servers", "examples", "securityRequirements",
        }},
    )


def _resolve_skills_root(skills_dir: str | Path) -> Path:
    """Allow callers to pass either ``.ai`` or ``.ai/skills``."""
    p = Path(skills_dir)
    if (p / "skills").is_dir():
        return p / "skills"
    return p


def load_a2a_skills(skills_dir: str | Path, agent_id: str) -> list[Any]:
    """Convenience: :func:`load_skills` projected to typed AgentSkill list.

    The result is suitable for direct use in
    :class:`a2a.agent_card.AgentCardInputs.skills`.
    """
    return [s.to_a2a_skill() for s in load_skills(skills_dir, agent_id)]


def load_skills(skills_dir: str | Path, agent_id: str) -> list[Skill]:
    """Read every ``<skills_dir>/<id>/SKILL.md`` filtered to ``agent_id``.

    Pass ``agent_id="*"`` (or ``""``) to disable filtering — used by the
    skills-manager catalogue endpoint to enumerate every skill regardless
    of its declared ``agents:`` list.
    """
    root = _resolve_skills_root(skills_dir)
    if not root.is_dir():
        logger.info("skills dir %s does not exist; no skills loaded", root)
        return []
    show_all = agent_id in {"*", ""}
    out: list[Skill] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "SKILL.md"
        if not manifest.is_file():
            continue
        skill = _skill_from_path(manifest)
        if skill is None:
            continue
        if not show_all and not skill.matches_agent(agent_id):
            continue
        out.append(skill)
    logger.info(
        "loaded %d skills for agent=%s from %s",
        len(out),
        agent_id,
        root,
    )
    return out


def _load_global_doc(skills_dir: str | Path, name: str) -> str:
    """Read ``<repo>/.ai/<name>`` if present (relative to ``skills_dir``)."""
    base = Path(skills_dir)
    if base.name == "skills":
        base = base.parent
    candidate = base / name
    if not candidate.is_file():
        return ""
    try:
        return _read_text(candidate)
    except OSError:
        return ""


# ── composers ────────────────────────────────────────────────────────────────
def compose_system_prompt(
    skills_dir: str | Path,
    agent_id: str,
    skills: list[Skill] | None = None,
) -> str:
    """Build the full system prompt: AGENTS.md + router.md + every skill."""
    if skills is None:
        skills = load_skills(skills_dir, agent_id)
    parts: list[str] = []
    agents_md = _load_global_doc(skills_dir, "AGENTS.md")
    if agents_md.strip():
        parts.append(agents_md.rstrip())
    router_md = _load_global_doc(skills_dir, "router.md")
    if router_md.strip():
        parts.append("\n\n---\n\n# router.md\n\n" + router_md.rstrip())
    for skill in skills:
        block = (
            f"\n\n---\n\n# Skill: {skill.id} (v{skill.version})\n"
            f"_tags: {', '.join(skill.tags) or '-'}_  \n"
            f"_triggers: {', '.join(skill.triggers) or '-'}_\n\n"
            f"{skill.body.strip()}"
        )
        parts.append(block)
    return "\n".join(parts).strip() + "\n"


def merge_mcp_servers(
    skills: Iterable[Skill],
    base: Iterable[Any] | None = None,
) -> list[Any]:
    """Union skill-declared servers with the existing base list, dedup-by-name.

    ``base`` may be a list of strings (server names) or a list of dicts
    with a ``name`` field (the canonical OpenHands ``MCP_SERVERS`` JSON
    shape). We preserve the input shape: if any base entry is a dict,
    skill-only entries are emitted as ``{"name": "<id>"}``; otherwise
    the result is a flat string list.
    """
    base_list = list(base or [])
    has_dicts = any(isinstance(x, dict) for x in base_list)

    seen: set[str] = set()
    out: list[Any] = []

    def _name_of(entry: Any) -> str:
        if isinstance(entry, dict):
            return str(entry.get("name") or entry.get("id") or "").strip()
        return str(entry).strip()

    for entry in base_list:
        n = _name_of(entry)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(entry)

    for skill in skills:
        for srv in skill.mcp_servers:
            n = str(srv).strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append({"name": n} if has_dicts else n)
    return out


def skills_version(skills: Iterable[Skill]) -> str:
    """sha256[:12] of all manifests (stable order). Used as Agent Card version."""
    h = hashlib.sha256()
    for skill in sorted(skills, key=lambda s: s.id):
        h.update(skill.id.encode("utf-8"))
        h.update(b"\0")
        h.update(skill.raw_manifest.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:12]


# ── env helper ───────────────────────────────────────────────────────────────
def default_skills_dir() -> str:
    """Resolve the conventional skills dir for adapters.

    Order:
    1. ``AGENT_MESH_SKILLS_DIR`` env (explicit override)
    2. ``SKILLS_DIR`` env (matches the compose default in
       ``compose.agents-mesh.yml``)
    3. ``/workspace/project/.ai`` (the adapter container mount point)
    4. ``./.ai`` next to the running process (dev shell)
    """
    for var in ("AGENT_MESH_SKILLS_DIR", "SKILLS_DIR"):
        explicit = os.environ.get(var, "").strip()
        if explicit:
            return explicit
    container_default = "/workspace/project/.ai"
    if Path(container_default).is_dir():
        return container_default
    return str(Path.cwd() / ".ai")
