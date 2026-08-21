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

from bsbot.matrix.runner import LOOP_SLEEP_TIME_MS, is_token_rejected_error


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
