"""Embedding generation — see ``specs/007-retrieval.md``.

Three things here are load-bearing:

* **Task types.** ``RETRIEVAL_DOCUMENT`` for chunks and ``RETRIEVAL_QUERY`` for
  questions. The model produces deliberately different vectors for each, and using
  one type for both measurably weakens retrieval.
* **Normalisation.** ``gemini-embedding-001`` only returns unit vectors at its full
  3072 dimensions. At the 768 we use, measured norm is ≈ 0.58, so we normalise before
  storing — otherwise cosine ranking is quietly wrong.
* **Caching by content hash.** Re-chunking or re-indexing unchanged text must not
  cost API calls; only genuinely new text is sent.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import structlog

from bsbot.index.store import Store, content_sha256, normalise

log = structlog.get_logger(__name__)

TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

#: How much of each text to show in the "embed.request" log line. Long enough to
#: recognise the chunk, short enough that a 100-item batch stays readable.
LOG_PREVIEW_CHARS = 100

#: Google meters embeddings by *items* per minute, not requests. Measured against
#: the live API: 3000/min for gemini-embedding-001. Staying under it is what keeps a
#: full re-index from losing chunks to 429s.
DEFAULT_ITEMS_PER_MINUTE = 2500

#: Transient API conditions worth retrying. A 400 means the request itself is wrong,
#: so retrying only wastes quota.
_RETRYABLE_RE = re.compile(
    r"\b(429|500|502|503|504|RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED)\b", re.I
)
#: Google tells us how long to wait; honour it rather than guessing.
_RETRY_DELAY_RE = re.compile(r"retry in ([0-9.]+)s", re.I)


def _is_retryable(error: Exception) -> bool:
    return bool(_RETRYABLE_RE.search(str(error)))


def _suggested_delay(error: Exception) -> float | None:
    match = _RETRY_DELAY_RE.search(str(error))
    return float(match.group(1)) if match else None


def _preview(text: str) -> str:
    """Trim a text for the log so one giant chunk cannot flood the output."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= LOG_PREVIEW_CHARS:
        return collapsed
    return collapsed[:LOG_PREVIEW_CHARS].rstrip() + "…"


class EmbedAPI(Protocol):
    def embed(self, *, texts: list[str], task_type: str, dim: int) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(
        self,
        api: EmbedAPI,
        *,
        store: Store,
        model: str,
        dim: int,
        batch_size: int = 100,
        rpm: int = 90,
        items_per_minute: int = DEFAULT_ITEMS_PER_MINUTE,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._store = store
        self._model = model
        self._dim = dim
        self._batch_size = max(1, batch_size)
        self._min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self._items_per_minute = items_per_minute
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request = 0.0
        #: (timestamp, item_count) pairs inside the trailing 60 s window.
        self._recent: list[tuple[float, int]] = []

    def embed_query(self, text: str) -> list[float]:
        result = self._embed([text], TASK_QUERY, skip_failures=False)
        vector = result[0]
        assert vector is not None
        return vector

    def embed_documents(
        self, texts: Sequence[str], *, skip_failures: bool = False
    ) -> list[list[float] | None]:
        return self._embed(list(texts), TASK_DOCUMENT, skip_failures=skip_failures)

    def _embed(
        self, texts: list[str], task_type: str, *, skip_failures: bool
    ) -> list[list[float] | None]:
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []

        for index, text in enumerate(texts):
            digest = content_sha256(text)
            cached = self._store.cached_embedding(digest, self._model, self._dim, task_type)
            if cached is not None:
                results[index] = cached
            else:
                pending.append((index, text))

        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            batch_texts = [t for _, t in batch]
            # What the user actually wants to see: not "POST ... 200 OK", but the
            # real strings a vector is about to be created for.
            log.info(
                "embed.request",
                task_type=task_type,
                count=len(batch_texts),
                chars=sum(len(t) for t in batch_texts),
                texts=[_preview(t) for t in batch_texts],
            )
            try:
                vectors = self._embed_batch(batch_texts, task_type)
            except Exception as exc:
                # AC-6: a failed batch must not discard the successful ones.
                if not skip_failures:
                    raise
                log.warning("embed.batch_failed", size=len(batch), error=str(exc))
                continue

            for (index, text), vector in zip(batch, vectors, strict=True):
                unit = normalise(vector)
                results[index] = unit
                self._store.cache_embedding(
                    content_sha256(text), self._model, self._dim, task_type, unit
                )
        return results

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        """One API call, throttled, with bounded retries on transient failures."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._throttle(len(texts))
            try:
                return self._api.embed(texts=texts, task_type=task_type, dim=self._dim)
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc) or attempt >= self._max_retries:
                    raise
                delay = _suggested_delay(exc) or min(2.0**attempt, 60.0)
                log.info(
                    "embed.retrying",
                    attempt=attempt + 1,
                    delay=round(delay, 1),
                    size=len(texts),
                )
                self._sleep(delay)
                # A quota window has passed; the old budget no longer applies.
                self._recent.clear()
        assert last_error is not None
        raise last_error

    def _throttle(self, items: int) -> None:
        """Respect both the per-request and the per-item budgets (AC-4).

        The per-item budget is the one that matters: Google meters embeddings by
        items per minute, so 40 requests of 100 texts blows a 3000/min quota even
        though it is only 40 requests.
        """
        if self._min_interval > 0:
            elapsed = self._clock() - self._last_request
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)

        if self._items_per_minute > 0:
            now = self._clock()
            self._recent = [(t, n) for t, n in self._recent if now - t < 60.0]
            used = sum(n for _, n in self._recent)
            if used + items > self._items_per_minute and self._recent:
                oldest = self._recent[0][0]
                wait = 60.0 - (now - oldest)
                if wait > 0:
                    log.info("embed.throttling", seconds=round(wait, 1), used=used)
                    self._sleep(wait)
                    self._recent.clear()
            self._recent.append((self._clock(), items))

        self._last_request = self._clock()
