"""Verifies spec 007 AC-1..AC-6 — embeddings."""

from __future__ import annotations

from pathlib import Path

import pytest

from bsbot.index.store import Store
from bsbot.llm.embed import GeminiEmbedder


class FakeGenAI:
    """Records requests; returns a deterministic vector per text."""

    def __init__(self, fail_on: set[str] | None = None, dim: int = 4) -> None:
        self.batches: list[list[str]] = []
        self.task_types: list[str] = []
        self._fail_on = fail_on or set()
        self._dim = dim

    def embed(self, *, texts: list[str], task_type: str, dim: int) -> list[list[float]]:
        self.batches.append(list(texts))
        self.task_types.append(task_type)
        if any(t in self._fail_on for t in texts):
            raise RuntimeError("500 backend error")
        # Un-normalised on purpose: this is what Gemini does at reduced dims.
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "index.db", embed_dim=4) as s:
        yield s


def make(store: Store, api: FakeGenAI, **kw) -> GeminiEmbedder:
    # sleep is stubbed out: tests must never actually wait on retry backoff.
    defaults = dict(
        model="gemini-embedding-001",
        dim=4,
        batch_size=2,
        rpm=0,
        sleep=lambda _: None,
        max_retries=2,
    )
    return GeminiEmbedder(api, store=store, **{**defaults, **kw})  # type: ignore[arg-type]


class TestTaskTypes:
    def test_documents_and_queries_use_different_task_types(self, store: Store) -> None:
        """AC-1: using one task type for both measurably degrades retrieval."""
        api = FakeGenAI()
        embedder = make(store, api)
        embedder.embed_documents(["ein dokument"])
        embedder.embed_query("eine frage")
        assert api.task_types == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


class TestNormalisation:
    def test_vectors_are_normalised(self, store: Store) -> None:
        """AC-2: Gemini returns norm ~0.58 at 768 dims; cosine needs unit vectors."""
        vector = make(store, FakeGenAI()).embed_documents(["abcdefgh"])[0]
        assert pytest.approx(sum(v * v for v in vector), abs=1e-6) == 1.0


class TestBatching:
    def test_texts_are_batched(self, store: Store) -> None:
        """AC-3: the API caps a request at 250 inputs / 20k tokens."""
        api = FakeGenAI()
        make(store, api, batch_size=2).embed_documents(["a", "b", "c", "d", "e"])
        assert [len(b) for b in api.batches] == [2, 2, 1]

    def test_empty_input_makes_no_request(self, store: Store) -> None:
        api = FakeGenAI()
        assert make(store, api).embed_documents([]) == []
        assert api.batches == []


class TestCaching:
    def test_repeated_text_is_not_re_embedded(self, store: Store) -> None:
        """AC-5: re-indexing unchanged text must cost nothing."""
        api = FakeGenAI()
        embedder = make(store, api)
        embedder.embed_documents(["wiederholt"])
        embedder.embed_documents(["wiederholt"])
        assert len(api.batches) == 1

    def test_cache_is_scoped_to_task_type(self, store: Store) -> None:
        """AC-5: a document vector must not be served for a query."""
        api = FakeGenAI()
        embedder = make(store, api)
        embedder.embed_documents(["gleicher text"])
        embedder.embed_query("gleicher text")
        assert len(api.batches) == 2

    def test_partial_cache_hit_only_requests_the_rest(self, store: Store) -> None:
        api = FakeGenAI()
        embedder = make(store, api, batch_size=10)
        embedder.embed_documents(["a"])
        embedder.embed_documents(["a", "b"])
        assert api.batches == [["a"], ["b"]]

    def test_results_stay_aligned_with_input_order(self, store: Store) -> None:
        """A cache hit in the middle must not shuffle the results."""
        api = FakeGenAI()
        embedder = make(store, api, batch_size=10)
        embedder.embed_documents(["bb"])
        vectors = embedder.embed_documents(["a", "bb", "ccc"])
        assert len(vectors) == 3
        lengths = [1, 2, 3]
        for vector, length in zip(vectors, lengths, strict=True):
            expected = length / (length**2 + 1) ** 0.5
            assert pytest.approx(vector[0], abs=1e-6) == expected


class TestResilience:
    def test_one_failed_batch_does_not_lose_the_others(self, store: Store) -> None:
        """AC-6"""
        api = FakeGenAI(fail_on={"boom"})
        embedder = make(store, api, batch_size=1)
        results = embedder.embed_documents(["ok1", "boom", "ok2"], skip_failures=True)
        assert results[0] is not None
        assert results[1] is None
        assert results[2] is not None

    def test_failure_raises_by_default(self, store: Store) -> None:
        embedder = make(store, FakeGenAI(fail_on={"boom"}), batch_size=1)
        with pytest.raises(RuntimeError):
            embedder.embed_documents(["boom"])


class TestRateLimiting:
    def test_requests_are_throttled_to_the_configured_rpm(self, store: Store) -> None:
        """AC-4: the free tier allows 100 RPM."""
        slept: list[float] = []
        api = FakeGenAI()
        rpm_ticks = iter(i * 0.1 for i in range(10000))
        embedder = GeminiEmbedder(
            api,
            store=store,
            model="m",
            dim=4,
            batch_size=1,
            rpm=60,  # type: ignore[arg-type]
            sleep=slept.append,
            clock=lambda: next(rpm_ticks),
        )
        embedder.embed_documents(["a", "b", "c"])
        assert any(s > 0 for s in slept), "expected throttling between requests"


class TestQuotaHandling:
    """Regression tests for a real failure against the live API.

    The first full embed run sent 40 batches x 100 texts while throttling only on
    *requests per minute*. Google's quota counts embedded *items* per minute
    (3000), so the run burned through it in seconds and lost 952 chunks.
    """

    def test_throttle_accounts_for_batch_size(self, store: Store) -> None:
        slept: list[float] = []
        ticks = iter([float(i) * 0.001 for i in range(200)])
        embedder = GeminiEmbedder(
            FakeGenAI(),
            store=store,
            model="m",
            dim=4,  # type: ignore[arg-type]
            batch_size=100,
            rpm=0,
            items_per_minute=300,
            sleep=slept.append,
            clock=lambda: next(ticks),
        )
        embedder.embed_documents([f"text {i}" for i in range(400)])
        assert any(s > 0 for s in slept), "expected item-based throttling to engage"

    def test_rate_limit_error_is_retried(self, store: Store) -> None:
        class FlakyAPI(FakeGenAI):
            def __init__(self) -> None:
                super().__init__()
                self.attempts = 0

            def embed(self, *, texts, task_type, dim):  # type: ignore[no-untyped-def]
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")
                return super().embed(texts=texts, task_type=task_type, dim=dim)

        api = FlakyAPI()
        embedder = GeminiEmbedder(
            api,
            store=store,
            model="m",
            dim=4,
            batch_size=10,
            rpm=0,  # type: ignore[arg-type]
            sleep=lambda _: None,
        )
        results = embedder.embed_documents(["a", "b"])
        assert api.attempts == 2
        assert all(r is not None for r in results)

    def test_retries_are_bounded(self, store: Store) -> None:
        class AlwaysLimited(FakeGenAI):
            def embed(self, *, texts, task_type, dim):  # type: ignore[no-untyped-def]
                self.batches.append(list(texts))
                raise RuntimeError("429 RESOURCE_EXHAUSTED")

        api = AlwaysLimited()
        embedder = GeminiEmbedder(
            api,
            store=store,
            model="m",
            dim=4,
            batch_size=10,
            rpm=0,  # type: ignore[arg-type]
            max_retries=2,
            sleep=lambda _: None,
        )
        results = embedder.embed_documents(["a"], skip_failures=True)
        assert results == [None]
        assert len(api.batches) == 3  # initial + 2 retries

    def test_non_retryable_error_is_not_retried(self, store: Store) -> None:
        class BadRequest(FakeGenAI):
            def embed(self, *, texts, task_type, dim):  # type: ignore[no-untyped-def]
                self.batches.append(list(texts))
                raise RuntimeError("400 INVALID_ARGUMENT")

        api = BadRequest()
        embedder = GeminiEmbedder(
            api,
            store=store,
            model="m",
            dim=4,
            batch_size=10,
            rpm=0,  # type: ignore[arg-type]
            max_retries=3,
            sleep=lambda _: None,
        )
        embedder.embed_documents(["a"], skip_failures=True)
        assert len(api.batches) == 1


class TestTransparencyLogging:
    """The user asked for this directly: httpx's 'HTTP Request: POST ... 200 OK'
    line says nothing about what actually happened. The log should show the real
    strings being embedded instead of a transport-level status line.
    """

    def test_embed_request_logs_the_actual_texts(self, store: Store) -> None:
        import structlog

        embedder = make(store, FakeGenAI(), batch_size=10)
        with structlog.testing.capture_logs() as logs:
            embedder.embed_documents(["Die Klausur ist am 15.03.2026.", "Zweiter Text."])

        events = [e for e in logs if e.get("event") == "embed.request"]
        assert events, f"expected an embed.request log event, got {logs}"
        assert events[0]["task_type"] == "RETRIEVAL_DOCUMENT"
        assert events[0]["count"] == 2
        assert "Die Klausur ist am 15.03.2026." in events[0]["texts"]

    def test_long_text_is_truncated_in_the_log_preview(self, store: Store) -> None:
        import structlog

        embedder = make(store, FakeGenAI(), batch_size=10)
        long_text = "A" * 500
        with structlog.testing.capture_logs() as logs:
            embedder.embed_documents([long_text])

        events = [e for e in logs if e.get("event") == "embed.request"]
        assert len(events[0]["texts"][0]) < 500

    def test_cache_hits_are_not_re_logged_as_requests(self, store: Store) -> None:
        import structlog

        embedder = make(store, FakeGenAI(), batch_size=10)
        embedder.embed_documents(["wiederholt"])
        with structlog.testing.capture_logs() as logs:
            embedder.embed_documents(["wiederholt"])

        assert not [e for e in logs if e.get("event") == "embed.request"]
