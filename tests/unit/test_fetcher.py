"""Verifies spec 005 — on-demand fetching and cache freshness.

Time is injected rather than slept, so TTL behaviour is tested deterministically.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from bsbot.index.store import Store
from bsbot.ingest.fetcher import DEFAULT_TTL_S, Fetcher, FetchOutcome

URL = "https://moodle.example.de/webservice/pluginfile.php/1/mod_resource/content/1/skript.pdf"
TOKEN = "tok-123"


class Clock:
    def __init__(self, now: int = 1_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "index.db", embed_dim=2) as s:
        yield s


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make_fetcher(store: Store, clock: Clock, **kw) -> Fetcher:
    return Fetcher(store, moodle_token=TOKEN, clock=clock, **kw)


class TestFirstFetch:
    @respx.mock
    async def test_downloads_and_records_metadata(self, store: Store, clock: Clock) -> None:
        """AC-2"""
        respx.get(URL).mock(
            return_value=httpx.Response(
                200,
                content=b"PDF-BYTES",
                headers={"ETag": 'W/"abc"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
            )
        )
        async with make_fetcher(store, clock) as f:
            result = await f.fetch(URL, moodle_timemodified=500)

        assert result.outcome is FetchOutcome.DOWNLOADED
        assert store.blob_path(result.sha256).read_bytes() == b"PDF-BYTES"  # type: ignore[arg-type]
        rec = store.fetch_record(URL)
        assert rec is not None
        assert rec.etag == 'W/"abc"'
        assert rec.last_modified == "Wed, 21 Oct 2026 07:28:00 GMT"
        assert rec.moodle_timemodified == 500
        assert rec.fetched_at == clock.now

    @respx.mock
    async def test_token_is_appended_for_moodle_urls(self, store: Store, clock: Clock) -> None:
        """AC-1: pluginfile.php needs the web service token."""
        route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
        async with make_fetcher(store, clock) as f:
            await f.fetch(URL, moodle_timemodified=1)
        assert route.calls.last.request.url.params["token"] == TOKEN

    @respx.mock
    async def test_token_is_not_sent_to_other_hosts(self, store: Store, clock: Clock) -> None:
        """AC-1: never leak the Moodle token to Nextcloud or anywhere else."""
        other = "https://cloud.example.de/index.php/s/AbC123/download"
        route = respx.get(other).mock(return_value=httpx.Response(200, content=b"x"))
        async with make_fetcher(store, clock) as f:
            await f.fetch(other, moodle_timemodified=1)
        assert "token" not in route.calls.last.request.url.params


class TestFreshnessLadder:
    @respx.mock
    async def test_within_ttl_makes_no_request(self, store: Store, clock: Clock) -> None:
        """AC-3: the cheap path — 544 MB must not move on every sync."""
        route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"data"))
        async with make_fetcher(store, clock) as f:
            await f.fetch(URL, moodle_timemodified=500)
            assert route.call_count == 1
            clock.advance(DEFAULT_TTL_S // 2)
            result = await f.fetch(URL, moodle_timemodified=500)

        assert result.outcome is FetchOutcome.CACHED
        assert route.call_count == 1

    @respx.mock
    async def test_changed_moodle_timestamp_forces_download(
        self, store: Store, clock: Clock
    ) -> None:
        """AC-4: the free signal from the structure crawl beats the TTL."""
        route = respx.get(URL).mock(
            side_effect=[
                httpx.Response(200, content=b"v1"),
                httpx.Response(200, content=b"v2"),
            ]
        )
        async with make_fetcher(store, clock) as f:
            await f.fetch(URL, moodle_timemodified=500)
            result = await f.fetch(URL, moodle_timemodified=900)

        assert route.call_count == 2
        assert result.outcome is FetchOutcome.CHANGED
        assert store.blob_path(result.sha256).read_bytes() == b"v2"  # type: ignore[arg-type]

    @respx.mock
    async def test_after_ttl_sends_conditional_request(self, store: Store, clock: Clock) -> None:
        """AC-5"""
        route = respx.get(URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    content=b"data",
                    headers={"ETag": 'W/"abc"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
                ),
                httpx.Response(304),
            ]
        )
        async with make_fetcher(store, clock) as f:
            await f.fetch(URL, moodle_timemodified=500)
            clock.advance(DEFAULT_TTL_S + 1)
            await f.fetch(URL, moodle_timemodified=500)

        headers = route.calls.last.request.headers
        assert headers["If-None-Match"] == 'W/"abc"'
        assert headers["If-Modified-Since"] == "Wed, 21 Oct 2026 07:28:00 GMT"

    @respx.mock
    async def test_304_refreshes_check_time_only(self, store: Store, clock: Clock) -> None:
        """AC-6: a few hundred bytes, and no re-extraction."""
        respx.get(URL).mock(
            side_effect=[
                httpx.Response(200, content=b"data", headers={"ETag": '"a"'}),
                httpx.Response(304),
            ]
        )
        async with make_fetcher(store, clock) as f:
            first = await f.fetch(URL, moodle_timemodified=500)
            clock.advance(DEFAULT_TTL_S + 1)
            second = await f.fetch(URL, moodle_timemodified=500)

        assert second.outcome is FetchOutcome.NOT_MODIFIED
        assert second.sha256 == first.sha256
        assert store.fetch_record(URL).checked_at == clock.now  # type: ignore[union-attr]

    @respx.mock
    async def test_identical_bytes_reuse_the_blob(self, store: Store, clock: Clock) -> None:
        """AC-7: a server that ignores validators must not trigger re-extraction."""
        respx.get(URL).mock(
            side_effect=[httpx.Response(200, content=b"same"), httpx.Response(200, content=b"same")]
        )
        async with make_fetcher(store, clock) as f:
            first = await f.fetch(URL, moodle_timemodified=500)
            clock.advance(DEFAULT_TTL_S + 1)
            second = await f.fetch(URL, moodle_timemodified=500)

        assert second.outcome is FetchOutcome.UNCHANGED
        assert second.sha256 == first.sha256
        assert store.blob_count() == 1

    @respx.mock
    async def test_different_bytes_store_a_new_blob(self, store: Store, clock: Clock) -> None:
        """AC-8"""
        respx.get(URL).mock(
            side_effect=[httpx.Response(200, content=b"old"), httpx.Response(200, content=b"new")]
        )
        async with make_fetcher(store, clock) as f:
            first = await f.fetch(URL, moodle_timemodified=500)
            clock.advance(DEFAULT_TTL_S + 1)
            second = await f.fetch(URL, moodle_timemodified=500)

        assert second.outcome is FetchOutcome.CHANGED
        assert second.sha256 != first.sha256
        assert store.blob_count() == 2


class TestFailures:
    @respx.mock
    async def test_http_error_is_recorded(self, store: Store, clock: Clock) -> None:
        """AC-9"""
        respx.get(URL).mock(return_value=httpx.Response(404))
        async with make_fetcher(store, clock) as f:
            result = await f.fetch(URL, moodle_timemodified=1)
        assert result.outcome is FetchOutcome.FAILED
        assert "404" in (store.fetch_record(URL).error or "")  # type: ignore[union-attr]

    @respx.mock
    async def test_failure_is_not_retried_during_cooldown(self, store: Store, clock: Clock) -> None:
        """AC-9: one dead link must not slow every sync."""
        route = respx.get(URL).mock(return_value=httpx.Response(404))
        async with make_fetcher(store, clock) as f:
            await f.fetch(URL, moodle_timemodified=1)
            clock.advance(60)
            result = await f.fetch(URL, moodle_timemodified=1)

        assert route.call_count == 1
        assert result.outcome is FetchOutcome.SKIPPED

    @respx.mock
    async def test_failure_is_retried_after_cooldown(self, store: Store, clock: Clock) -> None:
        route = respx.get(URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
        )
        async with make_fetcher(store, clock, failure_cooldown_s=100) as f:
            await f.fetch(URL, moodle_timemodified=1)
            clock.advance(101)
            result = await f.fetch(URL, moodle_timemodified=1)
        assert route.call_count == 2
        assert result.outcome is FetchOutcome.DOWNLOADED

    @respx.mock
    async def test_oversize_download_is_abandoned_while_streaming(
        self, store: Store, clock: Clock
    ) -> None:
        """AC-10: never buffer a 70 MB file just to discover it is too big."""
        respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 5000))
        async with make_fetcher(store, clock, max_bytes=1000) as f:
            result = await f.fetch(URL, moodle_timemodified=1)
        assert result.outcome is FetchOutcome.FAILED
        assert "too large" in (result.error or "").lower()
        assert store.blob_count() == 0

    @respx.mock
    async def test_no_partial_blob_survives_a_failure(self, store: Store, clock: Clock) -> None:
        """AC-12: a truncated file must never be trusted by a later run."""
        respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 5000))
        async with make_fetcher(store, clock, max_bytes=1000) as f:
            await f.fetch(URL, moodle_timemodified=1)
        assert list(store.blobs_dir.rglob("*")) == [] or all(
            p.is_dir() for p in store.blobs_dir.rglob("*")
        )


class TestConcurrency:
    @respx.mock
    async def test_fetch_concurrency_is_bounded(self, store: Store, clock: Clock) -> None:
        """AC-11"""
        import asyncio

        in_flight = peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx.Response(200, content=request.url.path.encode())

        respx.get(url__regex=r"https://moodle\.example\.de/.*").mock(side_effect=handler)
        async with make_fetcher(store, clock, max_concurrency=3) as f:
            await asyncio.gather(
                *(
                    f.fetch(f"https://moodle.example.de/file{i}.pdf", moodle_timemodified=1)
                    for i in range(12)
                )
            )
        assert peak <= 3
