"""Live access checks against the plugin's callback endpoint."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from moodle_llm_chat.access.moodle import MoodleAccessClient, MoodleUnavailable
from moodle_llm_chat.index.models import Site

SITE = Site(site_id="a", name="A", url="https://moodle.a.test", created_at=0)
CALLBACK = "https://moodle.a.test/local/llmchat/callback.php"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def client(http: httpx.AsyncClient, clock: Clock | None = None) -> MoodleAccessClient:
    return MoodleAccessClient(
        http,
        secrets=lambda site_id: ["mlc_secret"],
        cache_ttl_s=60,
        timeout_s=1.0,
        breaker_failures=2,
        breaker_reset_s=30,
        clock=clock or Clock(),
    )


@pytest.fixture
async def http() -> httpx.AsyncClient:
    return httpx.AsyncClient()


class TestCheck:
    @respx.mock
    async def test_returns_only_allowed_cmids(self, http: httpx.AsyncClient) -> None:
        route = respx.post(CALLBACK).respond(200, json={"allowed": [10]})
        allowed = await client(http).check(SITE, 42, {10, 11})
        assert allowed == {10}
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer mlc_secret"
        body = json.loads(request.content)
        assert body == {"action": "check_access", "userid": 42, "cmids": [10, 11]}

    @respx.mock
    async def test_decisions_are_cached_within_ttl(self, http: httpx.AsyncClient) -> None:
        clock = Clock()
        route = respx.post(CALLBACK).respond(200, json={"allowed": [10]})
        c = client(http, clock)
        await c.check(SITE, 42, {10, 11})
        assert await c.check(SITE, 42, {10, 11}) == {10}
        assert route.call_count == 1
        clock.t += 61
        await c.check(SITE, 42, {10})
        assert route.call_count == 2

    @respx.mock
    async def test_only_unknown_cmids_are_asked(self, http: httpx.AsyncClient) -> None:
        route = respx.post(CALLBACK).respond(200, json={"allowed": [10, 12]})
        c = client(http)
        await c.check(SITE, 42, {10})
        await c.check(SITE, 42, {10, 12})
        assert json.loads(route.calls.last.request.content)["cmids"] == [12]

    @respx.mock
    async def test_cache_is_per_user(self, http: httpx.AsyncClient) -> None:
        route = respx.post(CALLBACK).respond(200, json={"allowed": [10]})
        c = client(http)
        await c.check(SITE, 1, {10})
        await c.check(SITE, 2, {10})
        assert route.call_count == 2

    @respx.mock
    async def test_server_may_not_grant_more_than_asked(self, http: httpx.AsyncClient) -> None:
        respx.post(CALLBACK).respond(200, json={"allowed": [10, 99]})
        assert await client(http).check(SITE, 42, {10}) == {10}

    async def test_empty_request_makes_no_call(self, http: httpx.AsyncClient) -> None:
        assert await client(http).check(SITE, 42, set()) == set()


class TestFailClosed:
    @respx.mock
    async def test_moodle_down_raises(self, http: httpx.AsyncClient) -> None:
        respx.post(CALLBACK).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(MoodleUnavailable):
            await client(http).check(SITE, 42, {10})

    @respx.mock
    async def test_error_status_raises(self, http: httpx.AsyncClient) -> None:
        respx.post(CALLBACK).respond(500)
        with pytest.raises(MoodleUnavailable):
            await client(http).check(SITE, 42, {10})

    @respx.mock
    async def test_garbage_response_raises(self, http: httpx.AsyncClient) -> None:
        respx.post(CALLBACK).respond(200, text="<html>login</html>")
        with pytest.raises(MoodleUnavailable):
            await client(http).check(SITE, 42, {10})

    @respx.mock
    async def test_circuit_breaker_stops_calling_then_recovers(
        self, http: httpx.AsyncClient
    ) -> None:
        clock = Clock()
        route = respx.post(CALLBACK).respond(503)
        c = client(http, clock)
        for _ in range(2):
            with pytest.raises(MoodleUnavailable):
                await c.check(SITE, 42, {10})
        with pytest.raises(MoodleUnavailable, match="circuit"):
            await c.check(SITE, 42, {10})
        assert route.call_count == 2
        clock.t += 31
        route.respond(200, json={"allowed": [10]})
        assert await c.check(SITE, 42, {10}) == {10}

    @respx.mock
    async def test_previous_secret_is_tried_after_401(self, http: httpx.AsyncClient) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["authorization"] == "Bearer new":
                return httpx.Response(401)
            return httpx.Response(200, json={"allowed": [10]})

        respx.post(CALLBACK).mock(side_effect=handler)
        c = MoodleAccessClient(http, secrets=lambda s: ["new", "old"], clock=Clock())
        assert await c.check(SITE, 42, {10}) == {10}


class TestUserInfo:
    @respx.mock
    async def test_groups(self, http: httpx.AsyncClient) -> None:
        respx.post(CALLBACK).respond(200, json={"groups": {"2": ["Gruppe A"]}})
        info = await client(http).user_info(SITE, 42)
        assert info == {"groups": {2: ["Gruppe A"]}}
