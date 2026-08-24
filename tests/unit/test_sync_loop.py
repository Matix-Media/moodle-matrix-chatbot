"""Verifies the sync -> index -> embed cron loop.

sync_once/index_once/embed_once are thin wrappers around already-tested classes
(CourseCrawler, Indexer, GeminiEmbedder...) — mirroring how the `bsbot sync`/
`index`/`embed` CLI commands themselves are not tested directly either. What is
actually novel here, and what these tests cover, is the failure isolation
between steps and the interval loop.
"""

from __future__ import annotations

import structlog.testing

from bsbot.sync_loop import _run_steps, run_forever


class TestStepIsolation:
    """A Moodle outage during sync must not also cancel an otherwise-healthy
    embed pass over whatever was already indexed."""

    async def test_all_steps_run_even_if_one_fails(self) -> None:
        ran: list[str] = []

        async def ok(name: str) -> None:
            ran.append(name)

        async def boom() -> None:
            raise RuntimeError("moodle is down")

        await _run_steps(
            [
                ("sync", lambda: boom()),
                ("index", lambda: ok("index")),
                ("embed", lambda: ok("embed")),
            ]
        )

        assert ran == ["index", "embed"]

    async def test_a_failed_step_is_logged_by_name(self) -> None:
        async def boom() -> None:
            raise RuntimeError("moodle is down")

        with structlog.testing.capture_logs() as logs:
            await _run_steps([("sync", boom)])

        events = [e for e in logs if e.get("event") == "cron.step_failed"]
        assert events and events[0]["step"] == "sync"
        assert "moodle is down" in events[0]["error"]

    async def test_no_step_failing_logs_nothing(self) -> None:
        async def ok() -> None:
            return None

        with structlog.testing.capture_logs() as logs:
            await _run_steps([("sync", ok), ("index", ok), ("embed", ok)])

        assert not [e for e in logs if e.get("event") == "cron.step_failed"]


class TestRunForever:
    async def test_runs_the_requested_number_of_cycles(self) -> None:
        calls = 0

        async def cycle() -> None:
            nonlocal calls
            calls += 1

        await run_forever(cycle, interval_minutes=60, sleep=_no_sleep, max_cycles=3)

        assert calls == 3

    async def test_sleeps_between_cycles_for_the_configured_interval(self) -> None:
        sleeps: list[float] = []

        async def cycle() -> None:
            return None

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        await run_forever(cycle, interval_minutes=42, sleep=record_sleep, max_cycles=3)

        assert sleeps == [42 * 60, 42 * 60]  # never sleeps after the last cycle

    async def test_a_cycle_failure_propagates_rather_than_being_swallowed(self) -> None:
        """run_forever itself must not hide a bug in the cycle it is given — that
        isolation belongs to _run_steps, one layer down, not here."""

        async def cycle() -> None:
            raise RuntimeError("unexpected")

        try:
            await run_forever(cycle, interval_minutes=1, sleep=_no_sleep, max_cycles=1)
        except RuntimeError as exc:
            assert "unexpected" in str(exc)
        else:
            raise AssertionError("expected the cycle's exception to propagate")


async def _no_sleep(seconds: float) -> None:
    return None
