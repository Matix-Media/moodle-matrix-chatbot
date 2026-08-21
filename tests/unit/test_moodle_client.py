"""Verifies spec 002 — Moodle authentication, request layer and capability probe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from bsbot.config import MoodleConfig
from bsbot.moodle.client import MoodleClient
from bsbot.moodle.errors import (
    MoodleAPIError,
    MoodleAuthError,
    MoodleFunctionUnavailable,
    MoodleTransportError,
    MoodleWebServicesDisabled,
)

BASE = "https://moodle.example.de"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "moodle"

PASSWORD = "sup3rs3cret-pw"
TOKEN = "8e1b1f0c2d3a4b5c6d7e8f90a1b2c3d4"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def make_config(**overrides: Any) -> MoodleConfig:
    defaults: dict[str, Any] = {
        "base_url": BASE,
        "service": "moodle_mobile_app",
        "token": None,
        "username": "student",
        "password": SecretStr(PASSWORD),
        "timeout_s": 5.0,
        "max_retries": 3,
        "max_concurrency": 4,
        "retry_backoff_s": 0.0,  # keep retry tests instant
    }
    return MoodleConfig(**{**defaults, **overrides})


SITE_INFO = {
    "sitename": "iTech BS14",
    "username": "student",
    "userid": 4711,
    "release": "4.5.2 (Build: 20250210)",
    "version": "2024100702",
    "lang": "de",
    "functions": [
        {"name": "core_course_get_contents", "version": "2024100700"},
        {"name": "core_enrol_get_users_courses", "version": "2024100700"},
        {"name": "core_webservice_get_site_info", "version": "2024100700"},
    ],
}


# --------------------------------------------------------------------------- #
# Token acquisition
# --------------------------------------------------------------------------- #


class TestLogin:
    @respx.mock
    async def test_username_password_exchanged_for_token(self) -> None:
        """AC-1: credentials are exchanged at /login/token.php."""
        route = respx.post(f"{BASE}/login/token.php").mock(
            return_value=httpx.Response(200, json=fixture("token_success.json"))
        )
        async with MoodleClient(make_config()) as client:
            token = await client.login()

        assert token == TOKEN
        sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
        assert sent["username"] == "student"
        assert sent["password"] == PASSWORD
        assert sent["service"] == "moodle_mobile_app"

    @respx.mock
    async def test_configured_token_skips_login(self) -> None:
        """AC-2: a preconfigured token means no login request at all."""
        route = respx.post(f"{BASE}/login/token.php")
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            assert await client.login() == TOKEN
        assert not route.called

    @respx.mock
    async def test_invalid_login_returns_http_200_but_raises(self) -> None:
        """AC-3: the real trap — Moodle signals failure with HTTP 200 and an error body."""
        respx.post(f"{BASE}/login/token.php").mock(
            return_value=httpx.Response(200, json=fixture("token_error_invalidlogin.json"))
        )
        async with MoodleClient(make_config()) as client:
            with pytest.raises(MoodleAuthError) as exc:
                await client.login()
        assert exc.value.errorcode == "invalidlogin"

    @respx.mock
    async def test_web_services_disabled_is_a_distinct_error(self) -> None:
        """AC-4: this one is fixed inside Moodle, not in our config, so it gets its own type."""
        respx.post(f"{BASE}/login/token.php").mock(
            return_value=httpx.Response(200, json=fixture("token_error_wsdisabled.json"))
        )
        async with MoodleClient(make_config()) as client:
            with pytest.raises(MoodleWebServicesDisabled) as exc:
                await client.login()
        assert "advanced features" in str(exc.value).lower()

    @respx.mock
    async def test_credentials_never_appear_in_error_output(self) -> None:
        """AC-5: a traceback must never carry the password."""
        respx.post(f"{BASE}/login/token.php").mock(
            return_value=httpx.Response(200, json=fixture("token_error_invalidlogin.json"))
        )
        async with MoodleClient(make_config()) as client:
            with pytest.raises(MoodleAuthError) as exc:
                await client.login()
        assert PASSWORD not in str(exc.value)
        assert PASSWORD not in repr(exc.value)

    @respx.mock
    async def test_login_happens_only_once(self) -> None:
        """Two calls must not re-authenticate; repeated failed logins can lock the account."""
        route = respx.post(f"{BASE}/login/token.php").mock(
            return_value=httpx.Response(200, json=fixture("token_success.json"))
        )
        respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=SITE_INFO)
        )
        async with MoodleClient(make_config()) as client:
            await client.call("core_webservice_get_site_info")
            await client.call("core_webservice_get_site_info")
        assert route.call_count == 1


# --------------------------------------------------------------------------- #
# Request layer
# --------------------------------------------------------------------------- #


class TestRequests:
    @respx.mock
    async def test_token_is_sent_in_body_not_query_string(self) -> None:
        """AC-6: tokens in a query string end up in the school's access logs."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=SITE_INFO)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            await client.call("core_webservice_get_site_info")

        request = route.calls.last.request
        assert request.method == "POST"
        assert request.url.query == b""
        body = dict(httpx.QueryParams(request.content.decode()))
        assert body["wstoken"] == TOKEN
        assert body["wsfunction"] == "core_webservice_get_site_info"
        assert body["moodlewsrestformat"] == "json"

    @respx.mock
    async def test_list_parameters_are_php_encoded(self) -> None:
        """AC-7: end-to-end check that encode_params is actually wired in."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=[])
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            await client.call("core_course_get_courses_by_field", courseids=[2, 5])

        body = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
        assert body["courseids[0]"] == "2"
        assert body["courseids[1]"] == "5"

    @respx.mock
    async def test_moodle_exception_body_raises_api_error(self) -> None:
        """AC-8: Moodle reports application errors with HTTP 200 and an exception body."""
        respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "exception": "webservice_access_exception",
                    "errorcode": "accessexception",
                    "message": "Zugriff verweigert",
                },
            )
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            with pytest.raises(MoodleAPIError) as exc:
                await client.call("core_course_get_contents", courseid=2)
        assert exc.value.errorcode == "accessexception"

    @respx.mock
    async def test_invalid_token_surfaces_as_auth_error(self) -> None:
        """An expired token must be distinguishable from a permission problem."""
        respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "exception": "moodle_exception",
                    "errorcode": "invalidtoken",
                    "message": "Ungültiges Token",
                },
            )
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            with pytest.raises(MoodleAuthError):
                await client.call("core_course_get_contents", courseid=2)

    @respx.mock
    async def test_server_errors_are_retried_then_succeed(self) -> None:
        """AC-9: a flaky school server should not fail a whole sync."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(502),
                httpx.Response(200, json=SITE_INFO),
            ]
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            assert await client.call("core_webservice_get_site_info") == SITE_INFO
        assert route.call_count == 3

    @respx.mock
    async def test_timeouts_are_retried(self) -> None:
        """AC-9: connection timeouts are transient too."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            side_effect=[httpx.TimeoutException("slow"), httpx.Response(200, json=SITE_INFO)]
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            await client.call("core_webservice_get_site_info")
        assert route.call_count == 2

    @respx.mock
    async def test_retries_are_bounded(self) -> None:
        """AC-9: give up rather than hammering the server forever."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(503)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN), max_retries=2)) as client:
            with pytest.raises(MoodleTransportError):
                await client.call("core_webservice_get_site_info")
        assert route.call_count == 3  # initial attempt + 2 retries

    @respx.mock
    async def test_client_errors_are_not_retried(self) -> None:
        """AC-9: retrying a 404 just wastes the server's time."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(404)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            with pytest.raises(MoodleTransportError):
                await client.call("core_webservice_get_site_info")
        assert route.call_count == 1

    @respx.mock
    async def test_moodle_exceptions_are_not_retried(self) -> None:
        """AC-9: an access exception is deterministic; retrying cannot help."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(
                200, json={"exception": "x", "errorcode": "accessexception", "message": "no"}
            )
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            with pytest.raises(MoodleAPIError):
                await client.call("core_course_get_contents", courseid=2)
        assert route.call_count == 1

    @respx.mock
    async def test_concurrency_is_bounded(self) -> None:
        """AC-10: never open an unbounded number of connections to the school's server."""
        import asyncio

        in_flight = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx.Response(200, json=SITE_INFO)

        respx.post(f"{BASE}/webservice/rest/server.php").mock(side_effect=handler)
        async with MoodleClient(make_config(token=SecretStr(TOKEN), max_concurrency=2)) as client:
            await asyncio.gather(*(client.call("core_webservice_get_site_info") for _ in range(10)))
        assert peak <= 2


# --------------------------------------------------------------------------- #
# Capability probe
# --------------------------------------------------------------------------- #


class TestCapabilities:
    @respx.mock
    async def test_site_info_is_parsed_and_cached(self) -> None:
        """AC-11 / AC-13: probe once, expose identity and the function list."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=SITE_INFO)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            info = await client.site_info()
            again = await client.site_info()

        assert info.userid == 4711
        assert info.sitename == "iTech BS14"
        assert info.release.startswith("4.5.2")
        assert info.has("core_course_get_contents")
        assert not info.has("mod_forum_get_forums_by_courses")
        assert again is info
        assert route.call_count == 1

    @respx.mock
    async def test_unavailable_function_fails_without_a_request(self) -> None:
        """AC-12: don't waste a round trip discovering what the probe already told us."""
        route = respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=SITE_INFO)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            await client.site_info()
            calls_after_probe = route.call_count
            with pytest.raises(MoodleFunctionUnavailable) as exc:
                await client.call_if_available("mod_forum_get_forums_by_courses", courseids=[2])
        assert route.call_count == calls_after_probe
        assert "mod_forum_get_forums_by_courses" in str(exc.value)

    @respx.mock
    async def test_missing_functions_list_degrades_to_optimistic_mode(self) -> None:
        """AC-14: some sites restrict the function list; that must not break the crawler."""
        payload = {k: v for k, v in SITE_INFO.items() if k != "functions"}
        respx.post(f"{BASE}/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with MoodleClient(make_config(token=SecretStr(TOKEN))) as client:
            info = await client.site_info()
            assert info.optimistic is True
            assert info.has("literally_anything") is True
            # And a call proceeds rather than raising.
            await client.call_if_available("mod_forum_get_forums_by_courses", courseids=[2])
