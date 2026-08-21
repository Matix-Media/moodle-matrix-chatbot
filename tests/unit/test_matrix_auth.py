"""Spec 009 AC-15..AC-18 — how the bot authenticates.

nio's ``login()`` only ever sends the ``m.id.user`` identifier. Homeservers also
accept an e-mail as a third-party identifier, which is the right route when the
account's login name is not obvious or the MXID localpart is not what the user
thinks it is.
"""

from __future__ import annotations

from bsbot.matrix.runner import build_login_auth, looks_like_email


class TestEmailDetection:
    def test_email_is_recognised(self) -> None:
        assert looks_like_email("max@example.de")
        assert looks_like_email("max.mustermann@schule.hamburg.de")

    def test_mxid_is_not_an_email(self) -> None:
        """An MXID also contains '@' — it must not be mistaken for one."""
        assert not looks_like_email("@bsbot:matrix.org")
        assert not looks_like_email("@max:example.org")

    def test_bare_localpart_is_not_an_email(self) -> None:
        assert not looks_like_email("bsbot")


class TestAuthDict:
    def test_mxid_uses_the_user_identifier(self) -> None:
        """AC-15"""
        auth = build_login_auth("@bsbot:matrix.org", "pw", device_name="bsbot")
        assert auth["type"] == "m.login.password"
        assert auth["identifier"] == {"type": "m.id.user", "user": "@bsbot:matrix.org"}
        assert auth["password"] == "pw"
        assert auth["initial_device_display_name"] == "bsbot"

    def test_email_uses_the_thirdparty_identifier(self) -> None:
        """AC-15: the form nio's login() cannot produce."""
        auth = build_login_auth("max@example.de", "pw", device_name="bsbot")
        assert auth["identifier"] == {
            "type": "m.id.thirdparty",
            "medium": "email",
            "address": "max@example.de",
        }

    def test_password_is_never_placed_in_the_identifier(self) -> None:
        auth = build_login_auth("max@example.de", "secret", device_name="bsbot")
        assert "secret" not in str(auth["identifier"])
