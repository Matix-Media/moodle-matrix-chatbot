"""Verifies spec 009 AC-25/AC-26 — a real bug found live in production.

Timeline: the bot ran normally for ~6 minutes, then at 15:17:00 a single
``/sync`` response failed nio's own schema validation (missing ``next_batch``).
nio's ``verify()`` decorator (see ``nio/responses.py``) does not raise for this —
it converts the response into a ``SyncError`` and returns normally. Reading
``AsyncClient.sync_forever``'s source directly (``nio/client/async_client.py``)
confirmed the loop only pauses between iterations when ``loop_sleep_time`` is
explicitly set; a response that returns immediately (as an error body does, since
it never blocks on the server's long-poll) is retried with **zero delay**. The
runner was calling ``sync_forever(timeout=30_000, full_state=True)`` — no
``loop_sleep_time`` — so the single bad response triggered dozens of requests per
second against matrix.org until the process was killed by hand.
"""

from __future__ import annotations

import asyncio

import pytest

from bsbot.matrix.runner import (
    LOOP_SLEEP_TIME_MS,
    RESTART_BACKOFF_INITIAL_S,
    RESTART_BACKOFF_MAX_S,
    RESTART_HEALTHY_UPTIME_S,
    _run_with_restart,
    is_token_rejected_error,
)


class TestLoopSleepTimeIsConfigured:
    def test_a_non_zero_pacing_delay_is_defined(self) -> None:
        """AC-25: nio's sync_forever only self-paces via a *successful* response's
        long-poll wait. Any non-zero value here closes the zero-delay hot-loop gap;
        the exact number matters far less than it being set at all."""
        assert LOOP_SLEEP_TIME_MS > 0


class TestTokenRejectionDetection:
    """AC-26: only an unambiguous 'your token is not recognised' error should
    trigger a proactive refresh — a permission error (M_FORBIDDEN) can mean many
    things unrelated to token validity and must not trigger one.
    """

    def test_unknown_token_is_a_rejection(self) -> None:
        assert is_token_rejected_error("M_UNKNOWN_TOKEN") is True

    def test_missing_token_is_a_rejection(self) -> None:
        assert is_token_rejected_error("M_MISSING_TOKEN") is True

    def test_forbidden_is_not_treated_as_token_rejection(self) -> None:
        """M_FORBIDDEN can mean 'not in this room' or many other things — treating
        it as 'refresh the token' would be a guess, not a diagnosis."""
        assert is_token_rejected_error("M_FORBIDDEN") is False

    def test_none_is_not_a_rejection(self) -> None:
        assert is_token_rejected_error(None) is False

    def test_unrelated_code_is_not_a_rejection(self) -> None:
        assert is_token_rejected_error("M_LIMIT_EXCEEDED") is False


class TestSyncErrorHandling:
    """The runner must react to a SyncError, not just let nio log it and move on."""

    async def test_token_rejection_triggers_a_refresh(self) -> None:
        from bsbot.matrix.runner import MatrixRunner

        refreshed = []

        class FakeError:
            status_code = "M_UNKNOWN_TOKEN"
            message = "Invalid access token"

        class FakeClient:
            access_token = "stale"

        runner = MatrixRunner.__new__(MatrixRunner)  # bypass __init__: no real client needed
        runner._client = FakeClient()  # type: ignore[attr-defined]

        async def fake_refresh() -> str | None:
            refreshed.append(True)
            return "fresh-token"

        runner._refresh_token = fake_refresh  # type: ignore[method-assign]
        await runner._on_sync_error(FakeError())  # type: ignore[arg-type]

        assert refreshed == [True]
        assert runner._client.access_token == "fresh-token"  # type: ignore[attr-defined]

    async def test_unrelated_error_does_not_trigger_a_refresh(self) -> None:
        from bsbot.matrix.runner import MatrixRunner

        refreshed = []

        class FakeError:
            status_code = "M_LIMIT_EXCEEDED"
            message = "Too many requests"

        runner = MatrixRunner.__new__(MatrixRunner)
        runner._client = None  # type: ignore[attr-defined]

        async def fake_refresh() -> str | None:
            refreshed.append(True)
            return None

        runner._refresh_token = fake_refresh  # type: ignore[method-assign]
        await runner._on_sync_error(FakeError())  # type: ignore[arg-type]

        assert refreshed == []

    async def test_a_refresh_that_fails_raises_instead_of_retrying_silently(self) -> None:
        """Regression: a revoked/expired refresh token can never succeed, so
        returning quietly here left sync_forever retrying it on every single
        iteration forever (observed live: roughly once a second). Raising lets
        nio's own ``except: raise`` in sync_forever hand control to
        _run_with_restart's backoff instead.
        """
        from bsbot.matrix.runner import MatrixRunner

        class FakeError:
            status_code = "M_UNKNOWN_TOKEN"
            message = "Invalid access token"

        runner = MatrixRunner.__new__(MatrixRunner)
        runner._client = None  # type: ignore[attr-defined]

        async def fake_refresh() -> str | None:
            return None  # MAS rejected it -- refresh_access_token's failure mode

        runner._refresh_token = fake_refresh  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="matrix-login"):
            await runner._on_sync_error(FakeError())  # type: ignore[arg-type]


class TestRestartWithBackoff:
    """A laptop's lid closing, a VPS's network blipping — both tear a live
    connection down with no graceful notice, and nio does not catch the
    resulting exception (a real ``httpx.ReadError``, observed live). Without an
    outer restart loop, that kills the whole process; a long-running deployment
    needs to recover on its own, with nobody watching.
    """

    async def test_retries_after_a_failure_and_recovers(self) -> None:
        attempts = 0

        async def run_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("boom")

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        await _run_with_restart(run_once, sleep=fake_sleep, clock=lambda: 0.0)

        assert attempts == 3
        assert sleeps == [RESTART_BACKOFF_INITIAL_S, RESTART_BACKOFF_INITIAL_S * 2]

    async def test_backoff_is_capped(self) -> None:
        attempts = 0

        async def run_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 11:
                raise ConnectionError("boom")

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        await _run_with_restart(run_once, sleep=fake_sleep, clock=lambda: 0.0)

        assert max(sleeps) == RESTART_BACKOFF_MAX_S
        assert sleeps[-1] == RESTART_BACKOFF_MAX_S

    async def test_backoff_resets_after_a_healthy_run(self) -> None:
        """A crash hours into an otherwise-healthy run must not inherit whatever
        backoff a *previous, unrelated* incident had already climbed to."""
        attempts = 0
        clock_value = 0.0

        async def run_once() -> None:
            nonlocal attempts, clock_value
            attempts += 1
            if attempts in (1, 2):
                raise ConnectionError("boom")  # two quick failures: backoff climbs
            if attempts == 3:
                clock_value += RESTART_HEALTHY_UPTIME_S + 1  # a long healthy stretch
                raise ConnectionError("boom")
            # recovers on the 4th attempt

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        await _run_with_restart(run_once, sleep=fake_sleep, clock=lambda: clock_value)

        assert sleeps == [
            RESTART_BACKOFF_INITIAL_S,
            RESTART_BACKOFF_INITIAL_S * 2,
            RESTART_BACKOFF_INITIAL_S,  # reset, not a further doubling to *4
        ]

    async def test_cancellation_propagates_without_retrying(self) -> None:
        async def run_once() -> None:
            raise asyncio.CancelledError()

        async def fake_sleep(seconds: float) -> None:
            raise AssertionError("must not back off on cancellation")

        with pytest.raises(asyncio.CancelledError):
            await _run_with_restart(run_once, sleep=fake_sleep, clock=lambda: 0.0)
