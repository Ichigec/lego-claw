"""JWS / JWK helpers for signing the A2A Agent Card (Section 8.4).

The Agent Card contract requires the server to publish a *signed*
copy at ``/.well-known/agent-card.json`` (Section 8.4.2):

* The JSON body is canonicalised per **RFC 8785** (JCS).
* The canonical bytes are signed with **ES256** (ECDSA-P-256+SHA-256).
* The signature is attached as a detached JWS (``protected``,
  ``signature``) entry inside ``AgentCard.signatures``.
* The verifier obtains the matching public key by following the
  ``jku`` URI in the protected header (which by convention is
  ``/.well-known/jwks.json`` of the same host) and matching the
  ``kid`` claim.

This module provides the three primitives needed to wire that flow:

* :func:`load_or_generate_keypair` — bootstraps a per-adapter ES256
  keypair, persisting the private JWK to a host path so subsequent
  restarts pick the same identity.
* :func:`sign_agent_card` — produces the JWS over the canonicalised
  card and returns it as :class:`AgentCardSignature` (ready to slot
  into ``AgentCard.signatures``).
* :func:`build_jwks` — returns the public JWKS document served at
  ``/.well-known/jwks.json``.

Dependencies
============
We rely on ``cryptography`` (always present in the adapter image) for
the ECDSA primitives. RFC 8785 is implemented inline (:func:`jcs`)
because the spec only requires deterministic ordering of object keys
and minimal whitespace — Python's ``json`` module gets us 95% of the
way there. The remaining deviations (number formatting in particular)
do not matter for the small, integer-only Agent Card payload, but the
function is generic enough to be reused for any A2A-relevant document.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any

from .types import AgentCard, AgentCardSignature

logger = logging.getLogger("a2a.jws")


# ── base64url helpers ───────────────────────────────────────────────────────
def b64url(data: bytes) -> str:
    """RFC 4648 §5 base64url-without-padding, as required by JWS/JWK."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


# ── RFC 8785 (JCS) JSON canonicalisation ────────────────────────────────────
def _serialise_number(value: float | int) -> str:
    """Approximate ECMA-262 number serialisation used by RFC 8785."""
    if isinstance(value, bool):
        # ``bool`` subclasses ``int``; handle separately so True/False
        # stay as JSON booleans.
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError("RFC 8785 forbids NaN/Inf")
    if value == 0:
        return "0"
    # Python's repr() is good enough here for floats that round-trip
    # through json — Agent Cards have no high-precision floats.
    text = repr(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _serialise(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _serialise_number(value)
    if isinstance(value, str):
        # ``json.dumps`` already does the right escaping for RFC 8785
        # (it emits ASCII-safe \uXXXX for non-BMP and minimal escapes
        # for control characters); ``ensure_ascii=False`` keeps the
        # plain-ASCII range as-is which is what JCS expects.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialise(v) for v in value) + "]"
    if isinstance(value, dict):
        # JCS sorts keys by UTF-16 code unit order. For BMP-only keys
        # — which is the case for AgentCard — Python's lexicographic
        # ordering of str matches that.
        items = sorted(value.items(), key=lambda kv: kv[0])
        return (
            "{"
            + ",".join(
                json.dumps(k, ensure_ascii=False) + ":" + _serialise(v)
                for k, v in items
            )
            + "}"
        )
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def jcs(value: Any) -> bytes:
    """Canonicalise ``value`` per RFC 8785 (UTF-8 bytes)."""
    return _serialise(value).encode("utf-8")


# ── ES256 keypair management ────────────────────────────────────────────────
_keypair_lock = threading.Lock()


def _ensure_crypto() -> None:
    """Surface a clean error if the ``cryptography`` package is missing."""
    try:
        import cryptography  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "cryptography>=42 is required for A2A JWS signing — "
            "install it in the adapter image."
        ) from exc


def _kid(public_jwk: dict[str, str]) -> str:
    """RFC 7638 thumbprint, truncated to 8 hex chars per the plan."""
    canon = jcs({"crv": public_jwk["crv"], "kty": public_jwk["kty"], "x": public_jwk["x"], "y": public_jwk["y"]})
    return hashlib.sha256(canon).hexdigest()[:8]


def _ec_to_jwk(public_numbers: Any) -> dict[str, str]:
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    pub: dict[str, str] = {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(x),
        "y": b64url(y),
    }
    pub["kid"] = _kid(pub)
    pub["alg"] = "ES256"
    pub["use"] = "sig"
    return pub


def generate_keypair() -> dict[str, str]:
    """Return a fresh ES256 (P-256) JWK with the private ``d`` claim."""
    _ensure_crypto()
    from cryptography.hazmat.primitives.asymmetric import ec

    sk = ec.generate_private_key(ec.SECP256R1())
    pn = sk.public_key().public_numbers()
    pub = _ec_to_jwk(pn)
    d = sk.private_numbers().private_value.to_bytes(32, "big")
    return {**pub, "d": b64url(d)}


def public_jwk(jwk: dict[str, str]) -> dict[str, str]:
    """Strip the private claim from a JWK."""
    return {k: v for k, v in jwk.items() if k != "d"}


def load_or_generate_keypair(
    path: str | os.PathLike[str] | None,
) -> dict[str, str]:
    """Read a private JWK from ``path``; generate + persist if missing.

    If ``path`` is None or unwritable, generate an ephemeral key — fine
    for tests and short-lived containers, but the caller should set a
    persistent location in production so verifiers can reuse a stable
    ``kid``.
    """
    if path is None:
        return generate_keypair()
    p = Path(path)
    with _keypair_lock:
        if p.exists():
            try:
                jwk = json.loads(p.read_text(encoding="utf-8"))
                if {"kty", "crv", "x", "y", "d", "kid"} <= set(jwk):
                    return jwk
                logger.warning(
                    "private JWK at %s is malformed; regenerating", p
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "failed to read private JWK at %s; regenerating", p,
                    exc_info=True,
                )
        jwk = generate_keypair()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(jwk, indent=2, sort_keys=True), encoding="utf-8")
            try:
                p.chmod(0o600)
            except OSError:
                pass
        except OSError:
            logger.warning(
                "could not persist private JWK at %s; using ephemeral key",
                p,
                exc_info=True,
            )
        return jwk


# ── JWS signing (compact, detached payload optional) ────────────────────────
def _ec_signature_to_jws(der: bytes) -> bytes:
    """Convert an ECDSA DER signature to the raw R||S form (64 bytes)."""
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
    )

    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _sign_es256(private_jwk: dict[str, str], data: bytes) -> bytes:
    _ensure_crypto()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    d_bytes = b64url_decode(private_jwk["d"])
    sk = ec.derive_private_key(int.from_bytes(d_bytes, "big"), ec.SECP256R1())
    der = sk.sign(data, ec.ECDSA(hashes.SHA256()))
    return _ec_signature_to_jws(der)


def _verify_es256(public_jwk_d: dict[str, str], data: bytes, signature: bytes) -> bool:
    _ensure_crypto()
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        encode_dss_signature,
    )

    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    der = encode_dss_signature(r, s)
    x = int.from_bytes(b64url_decode(public_jwk_d["x"]), "big")
    y = int.from_bytes(b64url_decode(public_jwk_d["y"]), "big")
    pk = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    try:
        pk.verify(der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def sign_agent_card(
    card: AgentCard,
    private_jwk: dict[str, str],
    *,
    jku: str | None = None,
    typ: str = "JOSE",
) -> AgentCardSignature:
    """Sign ``card`` and return the detached JWS as
    :class:`AgentCardSignature`.

    The signature is computed over the *unsigned* canonical card —
    that is, ``card.signatures`` is set to ``None`` before
    canonicalisation per Section 8.4.2.
    """
    canonical = jcs(_card_for_signing(card))
    payload = b64url(canonical)
    protected = {"alg": "ES256", "typ": typ, "kid": private_jwk["kid"]}
    if jku:
        protected["jku"] = jku
    protected_b64 = b64url(jcs(protected))
    signing_input = f"{protected_b64}.{payload}".encode("ascii")
    sig = _sign_es256(private_jwk, signing_input)
    return AgentCardSignature(
        protected=protected_b64,
        signature=b64url(sig),
        header=None,
    )


def verify_agent_card(card: AgentCard, public_jwk_d: dict[str, str]) -> bool:
    """Verify *every* signature on ``card`` against ``public_jwk_d``.

    Returns ``True`` only if at least one signature in
    ``card.signatures`` matches.
    """
    if not card.signatures:
        return False
    canonical = jcs(_card_for_signing(card))
    payload = b64url(canonical)
    for sig in card.signatures:
        try:
            protected_bytes = b64url_decode(sig.protected)
            json.loads(protected_bytes.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        signing_input = f"{sig.protected}.{payload}".encode("ascii")
        signature_bytes = b64url_decode(sig.signature)
        if _verify_es256(public_jwk_d, signing_input, signature_bytes):
            return True
    return False


def _card_for_signing(card: AgentCard) -> dict[str, Any]:
    """Drop the ``signatures`` field before canonicalisation."""
    body = card.model_dump(by_alias=True, exclude_none=True)
    body.pop("signatures", None)
    return body


# ── JWKS document ───────────────────────────────────────────────────────────
def build_jwks(jwks: list[dict[str, str]] | dict[str, str]) -> dict[str, Any]:
    """Wrap one or more public JWKs into a JWKS document."""
    if isinstance(jwks, dict):
        jwks = [jwks]
    return {"keys": [public_jwk(jwk) for jwk in jwks]}


__all__ = [
    "b64url",
    "b64url_decode",
    "jcs",
    "generate_keypair",
    "load_or_generate_keypair",
    "public_jwk",
    "sign_agent_card",
    "verify_agent_card",
    "build_jwks",
]
