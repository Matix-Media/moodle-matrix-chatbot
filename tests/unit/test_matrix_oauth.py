"""OAuth device-grant login for next-gen Matrix auth (MAS/OIDC).

Why this exists: matrix.org accounts created via account.matrix.org have no legacy
password, and an access token borrowed from Element is bound to Element's device —
whose Olm private keys never leave that browser. The bot therefore cannot decrypt
anything. The device grant gives the bot a device of its own, with keys it controls.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from bsbot.matrix.oauth import (
    DeviceAuthorization,
    DeviceGrantError,
    build_client_metadata,
    generate_device_id,
    matrix_scope,
    poll_for_token,
    refresh_access_token,
    register_client,
    start_device_authorization,
)

ISSUER = "https://account.example.org"


class TestDeviceId:
    def test_is_ten_characters(self) -> None:
        assert len(generate_device_id()) == 10

    def test_is_unique_per_call(self) -> None:
        assert len({generate_device_id() for _ in range(50)}) == 50

    def test_uses_only_safe_characters(self) -> None:
        assert generate_device_id().isalnum()


class TestScope:
    def test_requests_api_access_and_a_specific_device(self) -> None:
        """MSC2967: the client picks its own device id and asks for it by scope.

        That is what makes the resulting device brand new and owned by us.
        """
        scope = matrix_scope("ABCDEFGHIJ")
        assert "urn:matrix:org.matrix.msc2967.client:api:*" in scope
        assert "urn:matrix:org.matrix.msc2967.client:device:ABCDEFGHIJ" in scope


class TestClientMetadata:
    def test_declares_the_device_grant_and_public_client(self) -> None:
        meta = build_client_metadata("bsbot", "https://example.org/bsbot")
        assert "urn:ietf:params:oauth:grant-type:device_code" in meta["grant_types"]
        assert meta["token_endpoint_auth_method"] == "none"
        assert meta["client_name"] == "bsbot"
        assert meta["application_type"] == "native"


class TestRegistration:
    @respx.mock
    async def test_returns_client_id(self) -> None:
        respx.post(f"{ISSUER}/oauth2/registration").mock(
            return_value=httpx.Response(201, json={"client_id": "01ABCDEF"})
        )
        async with httpx.AsyncClient() as http:
            client_id = await register_client(
                http, f"{ISSUER}/oauth2/registration", "bsbot", "https://example.org/bsbot"
            )
        assert client_id == "01ABCDEF"

    @respx.mock
    async def test_surfaces_the_servers_error_message(self) -> None:
        """MAS is picky about client metadata; hiding its reason wastes everyone's time."""
        respx.post(f"{ISSUER}/oauth2/registration").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_client_metadata",
                    "error_description": "client_uri must be https",
                },
            )
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(DeviceGrantError) as exc:
                await register_client(
                    http, f"{ISSUER}/oauth2/registration", "bsbot", "http://insecure"
                )
        assert "client_uri must be https" in str(exc.value)


class TestDeviceAuthorization:
    @respx.mock
    async def test_returns_the_user_facing_details(self) -> None:
        respx.post(f"{ISSUER}/oauth2/device").mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_code": "DC",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": f"{ISSUER}/link",
                    "verification_uri_complete": f"{ISSUER}/link?code=ABCD-EFGH",
                    "expires_in": 1200,
                    "interval": 5,
                },
            )
        )
        async with httpx.AsyncClient() as http:
            auth = await start_device_authorization(
                http, f"{ISSUER}/oauth2/device", "client", matrix_scope("ABCDEFGHIJ")
            )
        assert isinstance(auth, DeviceAuthorization)
        assert auth.user_code == "ABCD-EFGH"
        assert auth.verification_uri_complete.endswith("code=ABCD-EFGH")
        assert auth.interval == 5


class TestPolling:
    @respx.mock
    async def test_waits_while_authorization_is_pending(self) -> None:
        """authorization_pending is the normal state while the user is still clicking."""
        respx.post(f"{ISSUER}/oauth2/token").mock(
            side_effect=[
                httpx.Response(400, json={"error": "authorization_pending"}),
                httpx.Response(400, json={"error": "authorization_pending"}),
                httpx.Response(200, json={"access_token": "syt_abc", "token_type": "Bearer"}),
            ]
        )
        slept: list[float] = []
        async with httpx.AsyncClient() as http:
            token = await poll_for_token(
                http,
                f"{ISSUER}/oauth2/token",
                "client",
                "DC",
                interval=5,
                expires_in=60,
                sleep=slept.append,
                clock=iter(range(100)).__next__,
            )
        assert token.access_token == "syt_abc"
        assert len(slept) == 2

    @respx.mock
    async def test_slow_down_increases_the_interval(self) -> None:
        """The server asking us to back off must actually back off."""
        respx.post(f"{ISSUER}/oauth2/token").mock(
            side_effect=[
                httpx.Response(400, json={"error": "slow_down"}),
                httpx.Response(200, json={"access_token": "syt_abc"}),
            ]
        )
        slept: list[float] = []
        async with httpx.AsyncClient() as http:
            await poll_for_token(
                http,
                f"{ISSUER}/oauth2/token",
                "client",
                "DC",
                interval=5,
                expires_in=60,
                sleep=slept.append,
                clock=iter(range(100)).__next__,
            )
        assert slept[0] > 5

    @respx.mock
    async def test_denial_fails_immediately(self) -> None:
        respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(400, json={"error": "access_denied"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(DeviceGrantError) as exc:
                await poll_for_token(
                    http,
                    f"{ISSUER}/oauth2/token",
                    "client",
                    "DC",
                    interval=1,
                    expires_in=60,
                    sleep=lambda _: None,
                    clock=iter(range(100)).__next__,
                )
        assert "denied" in str(exc.value).lower()

    @respx.mock
    async def test_expiry_stops_polling(self) -> None:
        respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(400, json={"error": "authorization_pending"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(DeviceGrantError) as exc:
                await poll_for_token(
                    http,
                    f"{ISSUER}/oauth2/token",
                    "client",
                    "DC",
                    interval=1,
                    expires_in=3,
                    sleep=lambda _: None,
                    clock=iter([0, 1, 2, 3, 4, 5, 6]).__next__,
                )
        assert "expired" in str(exc.value).lower()


class TestTokenSet:
    """MAS issues short-lived access tokens plus a refresh token.

    Keeping only the access token means the bot dies within the hour and the human
    has to redo the browser flow — which defeats the point of deploying it.
    """

    @respx.mock
    async def test_poll_returns_refresh_token_and_expiry(self) -> None:
        respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "syt_abc",
                    "refresh_token": "mrt_xyz",
                    "expires_in": 300,
                    "token_type": "Bearer",
                },
            )
        )
        async with httpx.AsyncClient() as http:
            tokens = await poll_for_token(
                http,
                f"{ISSUER}/oauth2/token",
                "client",
                "DC",
                interval=1,
                expires_in=60,
                sleep=lambda _: None,
                clock=iter(range(100)).__next__,
            )
        assert tokens.access_token == "syt_abc"
        assert tokens.refresh_token == "mrt_xyz"
        assert tokens.expires_in == 300

    @respx.mock
    async def test_refresh_exchanges_the_refresh_token(self) -> None:
        route = respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "syt_new", "refresh_token": "mrt_new", "expires_in": 300},
            )
        )
        async with httpx.AsyncClient() as http:
            tokens = await refresh_access_token(http, f"{ISSUER}/oauth2/token", "client", "mrt_old")
        sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
        assert sent["grant_type"] == "refresh_token"
        assert sent["refresh_token"] == "mrt_old"
        assert tokens.access_token == "syt_new"

    @respx.mock
    async def test_rotated_refresh_token_is_returned(self) -> None:
        """MAS rotates refresh tokens; storing the old one would break the next refresh."""
        respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(
                200, json={"access_token": "a", "refresh_token": "rotated", "expires_in": 300}
            )
        )
        async with httpx.AsyncClient() as http:
            tokens = await refresh_access_token(http, f"{ISSUER}/oauth2/token", "c", "old")
        assert tokens.refresh_token == "rotated"

    @respx.mock
    async def test_refresh_failure_is_explicit(self) -> None:
        """An expired refresh token is the one case a human must redo the flow."""
        respx.post(f"{ISSUER}/oauth2/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(DeviceGrantError) as exc:
                await refresh_access_token(http, f"{ISSUER}/oauth2/token", "c", "dead")
        assert "matrix-login" in str(exc.value)


class TestRefreshScheduling:
    """A bot that refreshes only at startup dies when the token expires mid-run.

    matrix.org issues 4-hour tokens, so an unattended deployment must renew while
    running, not just on boot.
    """

    def test_renews_before_expiry_with_margin(self) -> None:
        from bsbot.matrix.oauth import seconds_until_refresh

        # Comfortably early, but not so early it hammers the endpoint.
        assert 0 < seconds_until_refresh(14400) < 14400
        assert seconds_until_refresh(14400) >= 14400 * 0.5

    def test_short_lived_tokens_still_get_a_sane_delay(self) -> None:
        from bsbot.matrix.oauth import seconds_until_refresh

        assert seconds_until_refresh(60) >= 30
        assert seconds_until_refresh(10) > 0

    def test_missing_expiry_falls_back_to_a_conservative_period(self) -> None:
        from bsbot.matrix.oauth import seconds_until_refresh

        assert seconds_until_refresh(0) > 0
