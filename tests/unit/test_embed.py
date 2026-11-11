"""CachingEmbedder: content-hash cache, batching, throttling, retries, normalisation."""

from __future__ import annotations

import math
from typing import Any

import pytest
import structlog
from langchain_core.embeddings import Embeddings

from moodle_llm_chat.llm.embed import (
    CachingEmbedder,
    EmbeddingDimensionError,
    content_sha256,
)

DIM = 3


class FakeEmbeddings(Embeddings):
    """Deterministic, un-normalised vectors; records every request."""

    def __init__(self, tag: float = 1.0, *, dim: int = DIM) -> None:
        self.tag = tag
        self.dim = dim
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        return [float(len(text)), self.tag, 2.0][: self.dim] + [0.0] * max(0, self.dim - 3)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class DictCache:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], list[float]] = {}

    def cached_embedding(self, text_sha256: str, key: str) -> list[float] | None:
        return self.data.get((text_sha256, key))

    def cache_embedding(self, text_sha256: str, key: str, vector: list[float]) -> None:
        self.data[(text_sha256, key)] = list(vector)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(
    *,
    query: Embeddings | None = None,
    document: Embeddings | None = None,
    cache: DictCache | None = None,
    clock: FakeClock | None = None,
    **kw: Any,
) -> tuple[CachingEmbedder, FakeEmbeddings, FakeEmbeddings, DictCache, FakeClock]:
    q = query or FakeEmbeddings(tag=1.0)
    d = document or FakeEmbeddings(tag=5.0)
    c = cache or DictCache()
    clk = clock or FakeClock()
    defaults: dict[str, Any] = {"batch_size": 2, "rpm": 0, "items_per_minute": 0}
    defaults.update(kw)
    emb = CachingEmbedder(
        query_api=q,
        document_api=d,
        cache=c,
        provider="fake",
        model="m",
        dim=DIM,
        sleep=clk.sleep,
        clock=clk.time,
        **defaults,
    )
    assert isinstance(q, FakeEmbeddings) and isinstance(d, FakeEmbeddings)
    return emb, q, d, c, clk


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


class TestTasks:
    async def test_queries_and_documents_use_separate_clients(self) -> None:
        emb, q, d, _, _ = make()
        await emb.embed_query("frage")
        await emb.embed_documents(["chunk"])
        assert q.calls == [["frage"]]
        assert d.calls == [["chunk"]]

    async def test_vectors_are_normalised(self) -> None:
        emb, *_ = make()
        vec = await emb.embed_query("abc")
        assert _norm(vec) == pytest.approx(1.0)

    async def test_wrong_dimension_is_an_error(self) -> None:
        emb, *_ = make(document=FakeEmbeddings(dim=5))
        with pytest.raises(EmbeddingDimensionError):
            await emb.embed_documents(["x"])


class TestBatching:
    async def test_texts_are_batched(self) -> None:
        emb, _, d, _, _ = make(batch_size=2)
        await emb.embed_documents(["a", "b", "c"])
        assert d.calls == [["a", "b"], ["c"]]

    async def test_empty_input_makes_no_request(self) -> None:
        emb, _, d, _, _ = make()
        assert await emb.embed_documents([]) == []
        assert d.calls == []


class TestCaching:
    async def test_repeated_text_is_not_re_embedded(self) -> None:
        emb, _, d, _, _ = make()
        first = await emb.embed_documents(["same"])
        second = await emb.embed_documents(["same"])
        assert d.calls == [["same"]]
        assert first == second

    async def test_cache_is_scoped_to_task(self) -> None:
        emb, q, d, _, _ = make()
        await emb.embed_documents(["t"])
        await emb.embed_query("t")
        assert d.calls == [["t"]] and q.calls == [["t"]]

    async def test_cache_key_includes_provider_model_and_dim(self) -> None:
        cache = DictCache()
        emb, *_ = make(cache=cache)
        await emb.embed_documents(["t"])
        ((digest, key),) = cache.data
        assert digest == content_sha256("t")
        assert key == "fake:m:3:document"

    async def test_partial_hit_requests_only_the_rest(self) -> None:
        emb, _, d, _, _ = make(batch_size=10)
        await emb.embed_documents(["a"])
        await emb.embed_documents(["a", "b"])
        assert d.calls == [["a"], ["b"]]

    async def test_results_stay_aligned_with_input_order(self) -> None:
        emb, *_ = make(batch_size=1)
        await emb.embed_documents(["bb"])
        out = await emb.embed_documents(["a", "bb", "cccc"])
        lens = [v[0] / _norm(v) for v in out if v is not None]
        expected = [n / _norm([n, 5.0, 2.0]) for n in (1.0, 2.0, 4.0)]
        assert lens == pytest.approx(expected)


class TestResilience:
    async def test_failed_batch_does_not_lose_the_others(self) -> None:
        class FailsOnB(FakeEmbeddings):
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                if "b" in texts:
                    raise RuntimeError("400 bad request")
                return super().embed_documents(texts)

        emb, *_ = make(document=FailsOnB(), batch_size=1)
        out = await emb.embed_documents(["a", "b", "c"], skip_failures=True)
        assert out[0] is not None and out[1] is None and out[2] is not None

    async def test_failure_raises_by_default(self) -> None:
        class Boom(FakeEmbeddings):
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("400 bad request")

        emb, *_ = make(document=Boom())
        with pytest.raises(RuntimeError):
            await emb.embed_documents(["a"])

    async def test_rate_limit_is_retried_with_suggested_delay(self) -> None:
        class Flaky(FakeEmbeddings):
            failures = 2

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                if self.failures:
                    self.failures -= 1
                    raise RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 7.5s.")
                return super().embed_documents(texts)

        emb, _, d, _, clk = make(document=Flaky())
        out = await emb.embed_documents(["a"])
        assert out[0] is not None
        assert clk.sleeps == [7.5, 7.5]
        assert d.calls == [["a"]]

    async def test_retries_are_bounded(self) -> None:
        class Always(FakeEmbeddings):
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("503 UNAVAILABLE")

        emb, _, _, _, clk = make(document=Always(), max_retries=2)
        with pytest.raises(RuntimeError, match="503"):
            await emb.embed_documents(["a"])
        assert len(clk.sleeps) == 2

    async def test_non_retryable_error_is_not_retried(self) -> None:
        class Bad(FakeEmbeddings):
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("400 INVALID_ARGUMENT")

        emb, _, _, _, clk = make(document=Bad())
        with pytest.raises(RuntimeError):
            await emb.embed_documents(["a"])
        assert clk.sleeps == []

    async def test_status_code_attribute_is_recognised(self) -> None:
        class RateLimited(Exception):
            status_code = 429

        class Flaky(FakeEmbeddings):
            failed = False

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                if not self.failed:
                    self.failed = True
                    raise RateLimited("slow down")
                return super().embed_documents(texts)

        emb, _, _, _, clk = make(document=Flaky())
        await emb.embed_documents(["a"])
        assert len(clk.sleeps) == 1


class TestThrottling:
    async def test_requests_per_minute(self) -> None:
        emb, _, _, _, clk = make(rpm=60, batch_size=1)
        await emb.embed_documents(["a", "b", "c"])
        # One request per second: the 2nd and 3rd wait a full interval.
        assert clk.sleeps == pytest.approx([1.0, 1.0])

    async def test_items_per_minute_accounts_for_batch_size(self) -> None:
        emb, _, _, _, clk = make(items_per_minute=4, batch_size=3)
        await emb.embed_documents(["a", "b", "c", "d", "e", "f"])
        assert len(clk.sleeps) == 1
        assert clk.sleeps[0] == pytest.approx(60.0)

    async def test_cache_hits_cost_nothing(self) -> None:
        emb, _, _, _, clk = make(items_per_minute=2, batch_size=2)
        await emb.embed_documents(["a", "b"])
        await emb.embed_documents(["a", "b"])
        assert clk.sleeps == []


class TestLogging:
    async def test_request_log_never_contains_text(self) -> None:
        """Content is encrypted at rest; logs must not become a plaintext copy."""
        emb, *_ = make()
        with structlog.testing.capture_logs() as logs:
            await emb.embed_documents(["Prüfungstermin für Max Mustermann"])
        assert logs, "expected an embed.request event"
        assert "Mustermann" not in repr(logs)
        request = next(e for e in logs if e["event"] == "embed.request")
        assert request["count"] == 1
