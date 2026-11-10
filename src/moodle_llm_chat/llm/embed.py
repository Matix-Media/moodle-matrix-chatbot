"""Provider-neutral embedding with a content-hash cache.

Load-bearing details:

* **Separate query/document clients.** Some providers (Gemini) produce deliberately
  different vectors per task type; using one for both weakens retrieval.
* **Normalisation.** Reduced-dimension vectors are often not unit length, so every
  vector is normalised before use or storage — otherwise cosine ranking is wrong.
* **Caching by content hash**, keyed on ``provider:model:dim:task``. Re-indexing
  unchanged text costs no API calls, and cache hits never count against rate limits.
* **Throttling** by requests *and* items per minute: some providers meter items,
  so 40 requests of 100 texts can blow a quota despite being only 40 requests.
* **Logs carry counts, never text** — the index is encrypted at rest, and a log line
  must not become a plaintext copy of course content.

Async throughout: network calls are awaited and the cache (the store) is touched
only from the event loop thread that owns the database connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, Protocol

import structlog
from langchain_core.embeddings import Embeddings

log = structlog.get_logger(__name__)

Task = Literal["query", "document"]

_RETRYABLE_RE = re.compile(
    r"\b(429|500|502|503|504|RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED|rate.?limit)\b",
    re.I,
)
_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)\s*s", re.I)
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class EmbeddingDimensionError(RuntimeError):
    """The provider returned vectors of a different size than configured."""


class EmbeddingCache(Protocol):
    def cached_embedding(self, text_sha256: str, key: str) -> list[float] | None: ...

    def cache_embedding(self, text_sha256: str, key: str, vector: list[float]) -> None: ...


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return list(vector)
    return [v / norm for v in vector]


def _is_retryable(error: BaseException) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    return bool(_RETRYABLE_RE.search(str(error)))


def _suggested_delay(error: BaseException) -> float | None:
    match = _RETRY_DELAY_RE.search(str(error))
    return float(match.group(1)) if match else None


class CachingEmbedder:
    def __init__(
        self,
        *,
        query_api: Embeddings,
        document_api: Embeddings,
        cache: EmbeddingCache,
        provider: str,
        model: str,
        dim: int,
        batch_size: int = 100,
        rpm: int = 90,
        items_per_minute: int = 2500,
        max_retries: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._apis: dict[Task, Embeddings] = {"query": query_api, "document": document_api}
        self._cache = cache
        self._provider = provider
        self._model = model
        self.dim = dim
        self._batch_size = max(1, batch_size)
        self._min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self._items_per_minute = items_per_minute
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        #: (timestamp, item_count) pairs inside the trailing 60 s window.
        self._recent: list[tuple[float, int]] = []

    def cache_key(self, task: Task) -> str:
        return f"{self._provider}:{self._model}:{self.dim}:{task}"

    async def embed_query(self, text: str) -> list[float]:
        (vector,) = await self._embed([text], "query", skip_failures=False)
        assert vector is not None
        return vector

    async def embed_documents(
        self, texts: Sequence[str], *, skip_failures: bool = False
    ) -> list[list[float] | None]:
        return await self._embed(list(texts), "document", skip_failures=skip_failures)

    async def _embed(
        self, texts: list[str], task: Task, *, skip_failures: bool
    ) -> list[list[float] | None]:
        if not texts:
            return []
        key = self.cache_key(task)
        results: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []
        for index, text in enumerate(texts):
            cached = self._cache.cached_embedding(content_sha256(text), key)
            if cached is not None:
                results[index] = cached
            else:
                pending.append((index, text))

        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            batch_texts = [t for _, t in batch]
            log.info(
                "embed.request",
                task=task,
                count=len(batch_texts),
                chars=sum(len(t) for t in batch_texts),
            )
            try:
                vectors = await self._embed_batch(batch_texts, task)
            except Exception as exc:
                if not skip_failures or isinstance(exc, EmbeddingDimensionError):
                    raise
                log.warning("embed.batch_failed", size=len(batch), error=type(exc).__name__)
                continue
            for (index, text), vector in zip(batch, vectors, strict=True):
                unit = normalise(vector)
                results[index] = unit
                self._cache.cache_embedding(content_sha256(text), key, unit)
        return results

    async def _embed_batch(self, texts: list[str], task: Task) -> list[list[float]]:
        api = self._apis[task]
        for attempt in range(self._max_retries + 1):
            await self._throttle(len(texts))
            try:
                vectors = await api.aembed_documents(texts)
            except Exception as exc:
                if not _is_retryable(exc) or attempt >= self._max_retries:
                    raise
                delay = _suggested_delay(exc) or min(2.0**attempt, 60.0)
                log.info("embed.retrying", attempt=attempt + 1, delay=round(delay, 1))
                await self._sleep(delay)
                # A quota window has passed; the old item budget no longer applies.
                self._recent.clear()
                continue
            for vector in vectors:
                if len(vector) != self.dim:
                    raise EmbeddingDimensionError(
                        f"{self._provider}/{self._model} returned {len(vector)}-dimensional "
                        f"vectors but MLC_EMBEDDING__DIM is {self.dim}"
                    )
            return vectors
        raise AssertionError("unreachable")  # pragma: no cover

    async def _throttle(self, items: int) -> None:
        if self._min_interval > 0 and self._last_request is not None:
            elapsed = self._clock() - self._last_request
            if elapsed < self._min_interval:
                await self._sleep(self._min_interval - elapsed)

        if self._items_per_minute > 0:
            now = self._clock()
            self._recent = [(t, n) for t, n in self._recent if now - t < 60.0]
            used = sum(n for _, n in self._recent)
            if used + items > self._items_per_minute and self._recent:
                wait = 60.0 - (now - self._recent[0][0])
                if wait > 0:
                    log.info("embed.throttling", seconds=round(wait, 1), used=used)
                    await self._sleep(wait)
                    self._recent.clear()
            self._recent.append((self._clock(), items))

        self._last_request = self._clock()
