"""Agent Card builder + the two well-known endpoints.

The Agent Card is the discovery document A2A clients fetch from
``/.well-known/agent-card.json`` (Section 8.2). We construct it from
three sources:

1. The :class:`agent_mesh_adapter.Runner` (``agent_id``, ``agent_label``).
2. A list of :class:`AgentSkill` produced by
   :mod:`skills_loader` — one entry per ``.ai/skills/<id>/SKILL.md``
   that opted into this agent via its ``agents:`` frontmatter field.
3. The transport bindings the adapter exposes — REST, JSON-RPC and
   gRPC — passed in as a list of ``(transport, url)`` pairs.

The card is JWS-signed (ES256) with the per-adapter private key and
the resulting signature attached to ``AgentCard.signatures``. The
public key is published at ``/.well-known/jwks.json`` so verifiers
can resolve ``kid``/``jku`` themselves.

Cache headers follow Section 8.6: a strong ``ETag`` derived from the
canonical card bytes plus a ``Cache-Control: public, max-age=60``
hint. ``version`` is stable for the lifetime of a given
(skills, bindings, agent identity) tuple — bumping any of those
changes the ETag and prompts clients to refresh.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import APIRouter, Header, HTTPException, Request, Response

from .jws import (
    b64url,
    build_jwks,
    jcs,
    load_or_generate_keypair,
    public_jwk,
    sign_agent_card,
)
from .types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)

logger = logging.getLogger("a2a.agent_card")


@dataclass
class AgentBinding:
    """One transport endpoint advertised in the card."""

    transport: str  # "HTTP+JSON" | "REST" | "JSONRPC" | "GRPC"
    url: str


@dataclass
class AgentCardInputs:
    """All adapter-side inputs the card builder needs.

    The :class:`build_agent_card` factory turns this dataclass into the
    final :class:`AgentCard`. Everything except ``name`` / ``url`` is
    optional so adapters with minimal config (the P0 wiring) can call
    the function with three arguments.
    """

    name: str
    description: str = ""
    url: str = ""  # canonical (preferred) base URL
    icon_url: str | None = None
    documentation_url: str | None = None
    provider: AgentProvider | None = None
    version_seed: str = ""  # arbitrary string folded into the version hash

    # Bindings — at least one must be present.
    bindings: list[AgentBinding] = field(default_factory=list)
    preferred_transport: str = "HTTP+JSON"

    # Skills — produced by skills_loader.
    skills: list[AgentSkill] = field(default_factory=list)

    # Capability flags & I/O modes.
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = field(
        default_factory=lambda: ["text/plain"]
    )
    default_output_modes: list[str] = field(
        default_factory=lambda: ["text/plain", "application/json"]
    )

    # Auth surface advertised in the card.
    security_schemes: dict[str, SecurityScheme] | None = None
    security: list[dict[str, list[str]]] | None = None

    supports_authenticated_extended_card: bool = False
    metadata: dict[str, Any] | None = None


# ── version hash (stable per inputs) ─────────────────────────────────────────
def _version_from_inputs(inputs: AgentCardInputs) -> str:
    """Derive ``AgentCard.version`` deterministically from the inputs.

    Per §3.4 of the plan: when skills change the version bumps so
    clients invalidate their cached card via ETag.
    """
    seed = {
        "n": inputs.name,
        "u": inputs.url,
        "d": inputs.description,
        "p": [
            {"t": b.transport, "u": b.url}
            for b in sorted(inputs.bindings, key=lambda x: (x.transport, x.url))
        ],
        "s": [
            sk.model_dump(by_alias=True, exclude_none=True)
            for sk in sorted(inputs.skills, key=lambda x: x.id)
        ],
        "c": inputs.capabilities.model_dump(by_alias=True, exclude_none=True),
        "x": inputs.version_seed,
    }
    digest = hashlib.sha256(jcs(seed)).hexdigest()[:12]
    return f"0.3.0+{digest}"


# ── card builder ────────────────────────────────────────────────────────────
def default_security_schemes(api_key_header: str = "Authorization") -> dict[str, SecurityScheme]:
    """Sensible default for the bearer-token auth used by the adapters."""
    return {
        "bearer": HTTPAuthSecurityScheme(scheme="bearer", description="Bearer token"),
        "apiKey": APIKeySecurityScheme(name=api_key_header, in_="header"),
    }


def build_agent_card(
    inputs: AgentCardInputs,
    *,
    private_jwk: dict[str, str] | None = None,
    jku: str | None = None,
    sign: bool = True,
) -> AgentCard:
    """Construct (and optionally sign) the Agent Card.

    ``private_jwk`` is mandatory when ``sign=True``. The caller is
    expected to obtain it from :func:`load_or_generate_keypair`.
    """
    if not inputs.bindings and not inputs.url:
        raise ValueError("AgentCardInputs needs either bindings or url")

    primary_url = inputs.url or inputs.bindings[0].url
    additional: list[AgentInterface] = []
    seen: set[tuple[str, str]] = set()
    for b in inputs.bindings:
        key = (b.transport, b.url)
        if key in seen:
            continue
        seen.add(key)
        additional.append(AgentInterface(transport=b.transport, url=b.url))  # type: ignore[arg-type]

    card = AgentCard(
        name=inputs.name,
        description=inputs.description,
        url=primary_url,
        icon_url=inputs.icon_url,
        documentation_url=inputs.documentation_url,
        provider=inputs.provider,
        version=_version_from_inputs(inputs),
        capabilities=inputs.capabilities,
        security_schemes=inputs.security_schemes or default_security_schemes(),
        security=inputs.security,
        default_input_modes=list(inputs.default_input_modes),
        default_output_modes=list(inputs.default_output_modes),
        skills=list(inputs.skills),
        additional_interfaces=additional or None,
        preferred_transport=inputs.preferred_transport,  # type: ignore[arg-type]
        supports_authenticated_extended_card=inputs.supports_authenticated_extended_card,
        metadata=inputs.metadata,
    )

    if sign:
        if private_jwk is None:
            raise ValueError("sign=True requires private_jwk")
        sig = sign_agent_card(card, private_jwk, jku=jku)
        card = card.model_copy(update={"signatures": [sig]})

    return card


# ── /.well-known endpoints ──────────────────────────────────────────────────
@dataclass
class AgentCardCache:
    """Memoised view of the canonical card bytes + ETag.

    Held in a module-level singleton so concurrent requests don't
    re-canonicalise/sign the card on every hit.
    """

    card: AgentCard
    body: bytes
    etag: str

    @classmethod
    def from_card(cls, card: AgentCard) -> "AgentCardCache":
        body = jcs(card.model_dump(by_alias=True, exclude_none=True))
        etag = hashlib.sha256(body).hexdigest()[:32]
        return cls(card=card, body=body, etag=f'"{etag}"')


class AgentCardManager:
    """Thread-safe holder for the active Agent Card.

    Reload semantics (Section §3.5 of the plan):

    * On boot, build the card once from ``inputs`` + skills.
    * On ``/admin/reload``, the adapter calls :meth:`reload` to recompute
      the card (skills may have changed). The new ETag flips and
      verifiers will refetch.
    """

    def __init__(
        self,
        *,
        build: Callable[[], AgentCard],
        extended_build: Callable[[], AgentCard] | None = None,
    ) -> None:
        self._build = build
        self._extended_build = extended_build
        self._lock = threading.Lock()
        self._cache: AgentCardCache | None = None
        self._extended_cache: AgentCardCache | None = None

    @property
    def cache(self) -> AgentCardCache:
        if self._cache is None:
            with self._lock:
                if self._cache is None:
                    self._cache = AgentCardCache.from_card(self._build())
        return self._cache

    def reload(self) -> AgentCard:
        with self._lock:
            self._cache = AgentCardCache.from_card(self._build())
            self._extended_cache = None
        return self._cache.card

    @property
    def extended_cache(self) -> AgentCardCache | None:
        if self._extended_build is None:
            return None
        if self._extended_cache is None:
            with self._lock:
                if self._extended_cache is None and self._extended_build is not None:
                    self._extended_cache = AgentCardCache.from_card(
                        self._extended_build()
                    )
        return self._extended_cache


def build_well_known_router(
    *,
    manager: AgentCardManager,
    public_jwks: list[dict[str, str]],
    extended_card_bearer_dep: Callable[..., Any] | None = None,
    cache_max_age_s: int = 60,
) -> APIRouter:
    """Return the FastAPI router that exposes the well-known endpoints."""
    router = APIRouter()

    @router.get(
        "/.well-known/agent-card.json",
        summary="A2A discovery document (Section 8.2)",
        include_in_schema=False,
    )
    async def well_known_agent_card(
        request: Request,
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ) -> Response:
        cache = manager.cache
        if if_none_match and if_none_match.strip() == cache.etag:
            return Response(status_code=304)
        return Response(
            content=cache.body,
            media_type="application/json",
            headers={
                "ETag": cache.etag,
                "Cache-Control": f"public, max-age={cache_max_age_s}",
            },
        )

    @router.get(
        "/.well-known/jwks.json",
        summary="Public keys used to verify Agent Card signatures.",
        include_in_schema=False,
    )
    async def well_known_jwks() -> Response:
        body = jcs(build_jwks(public_jwks))
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "ETag": etag,
                "Cache-Control": f"public, max-age={cache_max_age_s * 60}",
            },
        )

    if manager.extended_cache is not None or extended_card_bearer_dep is not None:
        deps = []
        if extended_card_bearer_dep is not None:
            from fastapi import Depends

            deps.append(Depends(extended_card_bearer_dep))

        @router.get(
            "/a2a/v1/extendedAgentCard",
            summary=(
                "Authenticated extended Agent Card (Section 8.5). "
                "Returns the same card by default; override "
                "AgentCardManager.extended_build for an enriched view."
            ),
            dependencies=deps,
        )
        async def extended_agent_card(
            request: Request,
            if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        ) -> Response:
            cache = manager.extended_cache or manager.cache
            if if_none_match and if_none_match.strip() == cache.etag:
                return Response(status_code=304)
            return Response(
                content=cache.body,
                media_type="application/json",
                headers={
                    "ETag": cache.etag,
                    "Cache-Control": "no-store",
                },
            )

    return router


# ── helper: typical adapter wiring in one call ──────────────────────────────
def make_card_manager(
    inputs: AgentCardInputs,
    *,
    private_jwk_path: str | os.PathLike[str] | None = None,
    jku: str | None = None,
    extended_inputs: AgentCardInputs | None = None,
) -> tuple[AgentCardManager, list[dict[str, str]]]:
    """One-shot helper for ``build_app`` to wire in the well-known endpoints.

    Returns the :class:`AgentCardManager` and the list of *public*
    JWKs that should be served at ``/.well-known/jwks.json``.
    """
    private = load_or_generate_keypair(private_jwk_path)
    pub = public_jwk(private)

    def _build() -> AgentCard:
        return build_agent_card(inputs, private_jwk=private, jku=jku, sign=True)

    extended_build: Callable[[], AgentCard] | None = None
    if extended_inputs is not None:
        ext = extended_inputs

        def _build_ext() -> AgentCard:
            return build_agent_card(ext, private_jwk=private, jku=jku, sign=True)

        extended_build = _build_ext

    return AgentCardManager(build=_build, extended_build=extended_build), [pub]


__all__ = [
    "AgentBinding",
    "AgentCardCache",
    "AgentCardInputs",
    "AgentCardManager",
    "build_agent_card",
    "build_well_known_router",
    "default_security_schemes",
    "make_card_manager",
]
