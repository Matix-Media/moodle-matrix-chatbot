"""Chat tokens: short-lived HS256 JWTs issued by the Moodle plugin for the browser.

* Key: ``HKDF-SHA256(ikm=site_secret, salt=None, info="chat-token", length=32)`` —
  the raw site secret is never used as a MAC key, and the browser never sees it.
  (PHP: ``hash_hkdf('sha256', $secret, 32, 'chat-token')``.)
* Header ``kid`` names the site; the token is verified with that site's current or,
  during a rotation grace period, previous secret. ``alg`` is pinned to HS256.
* Lifetime ≤ 15 minutes, issuer must equal the site's registered URL, and ``jti`` is
  single-use for state-changing websocket frames (``consume=True``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from moodle_llm_chat.access.scope import AccessScope, Capability, QuotaClaim, UserProfile
from moodle_llm_chat.index.models import Site
from moodle_llm_chat.index.store import Store, StoreError

ALGORITHM = "HS256"
MAX_TOKEN_LIFETIME_S = 900
REQUIRED_CLAIMS = ("iss", "sub", "iat", "exp", "jti", "courses", "cmids", "caps")


class TokenError(ValueError):
    """The chat token is invalid. The message is safe to log, not to show in detail."""


@dataclass(frozen=True)
class VerifiedToken:
    site: Site
    scope: AccessScope
    user: UserProfile
    quota: QuotaClaim | None
    claims: dict[str, Any]


def chat_token_key(secret: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"chat-token").derive(
        secret.encode("utf-8")
    )


def issue_chat_token(secret: str, site_id: str, claims: dict[str, Any]) -> str:
    """Sign a token the way the plugin does (tests and tooling)."""
    return jwt.encode(claims, chat_token_key(secret), algorithm=ALGORITHM, headers={"kid": site_id})


def verify_chat_token(
    token: str,
    store: Store,
    *,
    consume: bool = False,
    leeway_s: int = 30,
    now: int | None = None,
) -> VerifiedToken:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("malformed token") from exc
    if header.get("alg") != ALGORITHM:
        raise TokenError("unsupported algorithm")
    site_id = header.get("kid")
    if not isinstance(site_id, str):
        raise TokenError("token names no site")
    try:
        site = store.site(site_id)
    except StoreError as exc:
        raise TokenError("unknown site") from exc
    if site.revoked_at is not None:
        raise TokenError("site revoked")

    stamp = int(time.time()) if now is None else now
    claims: dict[str, Any] | None = None
    last_error: Exception | None = None
    for secret in store.site_secrets(site_id, now=stamp):
        try:
            claims = jwt.decode(
                token,
                chat_token_key(secret),
                algorithms=[ALGORITHM],
                issuer=site.url,
                leeway=leeway_s,
                options={"require": list(REQUIRED_CLAIMS)},
            )
            break
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenError("wrong issuer") from exc
        except jwt.PyJWTError as exc:
            last_error = exc
    if claims is None:
        raise TokenError("invalid signature or claims") from last_error

    if int(claims["exp"]) - int(claims["iat"]) > MAX_TOKEN_LIFETIME_S:
        raise TokenError("token lifetime too long")
    if consume and not store.consume_jti(site_id, str(claims["jti"]), int(claims["exp"])):
        raise TokenError("token replay")

    try:
        user_id = int(claims["sub"])
        courses = frozenset(int(c) for c in claims["courses"])
        cmids = frozenset(int(c) for c in claims["cmids"])
        roles = {int(k): [str(r) for r in v] for k, v in (claims.get("roles") or {}).items()}
    except (TypeError, ValueError) as exc:
        raise TokenError("malformed claims") from exc
    caps = frozenset(Capability(c) for c in claims["caps"] if c in Capability._value2member_map_)

    course_context = claims.get("course")
    quota = QuotaClaim.model_validate(claims["quota"]) if claims.get("quota") else None
    return VerifiedToken(
        site=site,
        scope=AccessScope(
            site_id=site_id, user_id=user_id, course_ids=courses, cmids=cmids, caps=caps
        ),
        user=UserProfile(
            user_id=user_id,
            fullname=str(claims.get("name") or ""),
            lang=str(claims.get("lang") or "de"),
            roles=roles,
            course_context=int(course_context) if course_context else None,
        ),
        quota=quota,
        claims=claims,
    )
