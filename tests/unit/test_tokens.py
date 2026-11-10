"""Chat tokens: HS256 JWTs the plugin signs with a key derived from the site secret."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
import pytest

from moodle_llm_chat.access.scope import Capability
from moodle_llm_chat.access.tokens import (
    MAX_TOKEN_LIFETIME_S,
    TokenError,
    chat_token_key,
    issue_chat_token,
    verify_chat_token,
)
from moodle_llm_chat.index.store import Store

VECTORS = Path(__file__).parent.parent / "fixtures" / "jwt_vectors.json"
SITE = "site-a"
URL = "https://moodle.a.test"


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": URL,
        "sub": "42",
        "name": "Erika Muster",
        "lang": "de",
        "caps": ["read", "interact"],
        "courses": [2, 3],
        "roles": {"2": ["student"], "3": ["student"]},
        "cmids": [10, 11, 20],
        "quota": {"day": 50000, "month": 500000, "exempt": False},
        "jti": f"j-{now}-{len(overrides)}",
        "iat": now,
        "exp": now + 600,
    }
    base.update(overrides)
    return base


@pytest.fixture
def secret(store: Store) -> str:
    return store.add_site(SITE, name="A", url=URL)


class TestKeyDerivation:
    def test_shared_vectors_match(self) -> None:
        """PHP (local_llmchat) consumes the same file — both sides must agree."""
        vectors = json.loads(VECTORS.read_text())
        for v in vectors["cases"]:
            assert chat_token_key(v["secret"]).hex() == v["derived_key_hex"]
            decoded = jwt.decode(
                v["token"],
                chat_token_key(v["secret"]),
                algorithms=["HS256"],
                options={"verify_exp": False, "verify_iat": False},
            )
            assert decoded == v["claims"]

    def test_key_is_not_the_secret(self) -> None:
        assert chat_token_key("mlc_x") != b"mlc_x"
        assert len(chat_token_key("mlc_x")) == 32


class TestVerify:
    def test_valid_token(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, SITE, _claims())
        verified = verify_chat_token(token, store)
        assert verified.site.site_id == SITE
        assert verified.scope.user_id == 42
        assert verified.scope.cmids == frozenset({10, 11, 20})
        assert verified.scope.course_ids == frozenset({2, 3})
        assert Capability.READ in verified.scope.caps
        assert verified.user.fullname == "Erika Muster"
        assert verified.user.roles == {2: ["student"], 3: ["student"]}
        assert verified.quota is not None and verified.quota.day == 50000

    def test_wrong_secret(self, store: Store, secret: str) -> None:
        token = issue_chat_token("mlc_other", SITE, _claims())
        with pytest.raises(TokenError):
            verify_chat_token(token, store)

    def test_expired(self, store: Store, secret: str) -> None:
        now = int(time.time())
        token = issue_chat_token(secret, SITE, _claims(iat=now - 900, exp=now - 300))
        with pytest.raises(TokenError, match="expired"):
            verify_chat_token(token, store)

    def test_lifetime_cap(self, store: Store, secret: str) -> None:
        now = int(time.time())
        token = issue_chat_token(
            secret, SITE, _claims(iat=now, exp=now + MAX_TOKEN_LIFETIME_S + 60)
        )
        with pytest.raises(TokenError, match="lifetime"):
            verify_chat_token(token, store)

    def test_issuer_must_be_the_site_url(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, SITE, _claims(iss="https://evil.test"))
        with pytest.raises(TokenError, match="issuer"):
            verify_chat_token(token, store)

    def test_alg_none_rejected(self, store: Store, secret: str) -> None:
        token = jwt.encode(_claims(), key=None, algorithm="none", headers={"kid": SITE})
        with pytest.raises(TokenError):
            verify_chat_token(token, store)

    def test_unknown_site(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, "nope", _claims())
        with pytest.raises(TokenError):
            verify_chat_token(token, store)

    def test_revoked_site(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, SITE, _claims())
        store.revoke_site(SITE)
        with pytest.raises(TokenError):
            verify_chat_token(token, store)

    def test_jti_is_single_use_when_consumed(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, SITE, _claims(jti="once"))
        verify_chat_token(token, store, consume=True)
        with pytest.raises(TokenError, match="replay"):
            verify_chat_token(token, store, consume=True)
        # Read-only use (e.g. listing conversations) does not burn the token.
        verify_chat_token(token, store, consume=False)

    def test_previous_secret_works_during_grace(self, store: Store, secret: str) -> None:
        store.rotate_site_secret(SITE, grace_s=3600)
        token = issue_chat_token(secret, SITE, _claims())
        assert verify_chat_token(token, store).site.site_id == SITE

    def test_missing_required_claim(self, store: Store, secret: str) -> None:
        claims = _claims()
        del claims["cmids"]
        with pytest.raises(TokenError):
            verify_chat_token(issue_chat_token(secret, SITE, claims), store)

    def test_unknown_capability_is_ignored(self, store: Store, secret: str) -> None:
        token = issue_chat_token(secret, SITE, _claims(caps=["read", "teleport"]))
        assert verify_chat_token(token, store).scope.caps == frozenset({Capability.READ})
