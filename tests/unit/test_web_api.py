"""Verifies spec 014 — web chat API."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bsbot.config import Settings
from bsbot.rag.pipeline import Answer
from bsbot.web.app import create_app
from bsbot.web.rate_limit import RateLimiter


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("BSBOT_WEB__API_TOKEN", "s3cret")
    monkeypatch.setenv("BSBOT_GEMINI__API_KEY", "dummy-key")
    monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
    return Settings(_env_file=None)  # type: ignore[call-arg]


class _FakePipeline:
    """Records the call it received and returns a canned, grounded answer —
    avoids AnswerPipeline.answer() actually reaching Gemini in these tests.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []

    def answer(self, question: str, *, history: list[tuple[str, str]] | None = None) -> Answer:
        self.calls.append({"question": question, "history": history})
        return Answer(text=f"answer to: {question}", grounded=True, citations=[])


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = _FakePipeline()
    monkeypatch.setattr("bsbot.web.app.AnswerPipeline", lambda *a, **k: fake)
    app = create_app(settings)
    with TestClient(app) as test_client:
        test_client.fake_pipeline = fake  # type: ignore[attr-defined]
        yield test_client


def test_healthz_needs_no_token(client: Any) -> None:
    """AC-3."""
    response = client.get("/healthz")
    assert response.status_code == 200


def test_ask_without_token_is_rejected(client: Any) -> None:
    """AC-1."""
    response = client.post("/api/ask", json={"question": "Wann ist die nächste Prüfung?"})
    assert response.status_code == 401


def test_ask_with_wrong_token_is_rejected(client: Any) -> None:
    """AC-2."""
    response = client.post(
        "/api/ask",
        json={"question": "Wann ist die nächste Prüfung?"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_ask_with_valid_token_returns_the_answer(client: Any) -> None:
    """AC-4."""
    response = client.post(
        "/api/ask",
        json={"question": "Wann ist die nächste Prüfung?"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "answer to: Wann ist die nächste Prüfung?"
    assert body["grounded"] is True


def test_history_round_trips_to_the_pipeline_unmodified(client: Any) -> None:
    """AC-5: the caller's resent history reaches pipeline.answer() as-is, since
    the API holds no session state of its own."""
    history = [["Wo ist Raum 204?", "Im Hauptgebäude, 2. OG."]]
    client.post(
        "/api/ask",
        json={"question": "Und wann ist da Unterricht?", "history": history},
        headers={"Authorization": "Bearer s3cret"},
    )
    call = client.fake_pipeline.calls[-1]
    assert call["history"] == [tuple(pair) for pair in history]


def test_rate_limit_exceeded_returns_429(client: Any) -> None:
    """AC-7."""
    client.app.state.limiter._max_per_minute = 1
    headers = {"Authorization": "Bearer s3cret"}
    ok = client.post("/api/ask", json={"question": "eine Frage"}, headers=headers)
    blocked = client.post("/api/ask", json={"question": "noch eine Frage"}, headers=headers)
    assert ok.status_code == 200
    assert blocked.status_code == 429


def test_every_route_is_async_to_keep_sqlite_on_one_thread() -> None:
    """Regression: a plain `def` endpoint is dispatched by FastAPI to a
    threadpool worker, which can land on a different thread than the one
    `lifespan` opened the sqlite3 connection on —
    sqlite3.ProgrammingError: "SQLite objects created in a thread can only
    be used in that same thread." Seen live in production (spec 014).
    `async def` keeps the whole request on the single event-loop thread —
    checked across every router (spec 015), not just `/api/ask`, since every
    one of them touches the same Store."""
    from bsbot.web.routes import ask, crawl, embed, fetch_cache, ingest_message, segments

    routers = (ask, crawl, embed, fetch_cache, ingest_message, segments)
    routes = [route for mod in routers for route in mod.router.routes]
    assert routes, "expected at least one route to check"
    for route in routes:
        assert inspect.iscoroutinefunction(route.endpoint), route.path


class TestRateLimiterUnit:
    """RateLimiter in isolation, no HTTP layer involved."""

    def test_burst_limit(self) -> None:
        limiter = RateLimiter(max_per_minute=2, max_per_day=1000)
        assert limiter.allow()
        assert limiter.allow()
        assert not limiter.allow()

    def test_daily_quota_blocks_after_the_limit(self) -> None:
        limiter = RateLimiter(max_per_minute=1000, max_per_day=1)
        assert limiter.allow()
        assert not limiter.allow()

    def test_daily_quota_resets_on_a_new_day(self) -> None:
        """Same injectable-clock pattern as test_matrix_bot.py's own daily-quota
        reset test — the quota is per calendar day (UTC), not a rolling window."""
        day1 = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, 0, 30, tzinfo=UTC)
        clock = {"now": day1}
        limiter = RateLimiter(max_per_minute=1000, max_per_day=1, now=lambda: clock["now"])
        assert limiter.allow()
        assert not limiter.allow()
        clock["now"] = day2
        assert limiter.allow()
