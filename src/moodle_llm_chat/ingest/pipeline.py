"""The content processing pipeline: persisted jobs, one worker pool per stage.

Stages differ in what they wait on, so each has its own concurrency and backoff and
a slow stage never blocks a fast one:

* ``fetch``   — external URL modules (YouTube, TaskCards, HackMD, Google, Nextcloud):
  fetch, extract, index. Network-bound.
* ``extract`` — inline text, HTML, and uploaded files: extract, chunk, index.
  CPU-bound work runs in a worker thread. A file whose blob has not arrived yet
  parks as ``waiting`` and is re-armed by :meth:`Pipeline.blob_arrived`.
* ``ocr``     — re-extracts files with scanned pages through the vision model.

Embedding is not a per-document job: one loop batches *all* chunks without a
vector, across documents, so a 300-page PDF and a one-line label share batches.
Its persisted state is simply "chunks without vectors".

Everything is resumable: jobs live in the store, ``running`` jobs are re-queued on
startup, each document's content is replaced atomically, and the embedding cache
makes retries cheap. All store access happens on the event loop thread; fetching
awaits, extraction runs via :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx
import structlog

from moodle_llm_chat.config import PipelineSection
from moodle_llm_chat.index.models import ChunkIn, Document, DocumentKind, Job, SegmentIn
from moodle_llm_chat.index.store import Store, StoreError
from moodle_llm_chat.ingest.chunk import Segment, chunk_segments
from moodle_llm_chat.ingest.extract import (
    EXTRACT_VERSION,
    ExtractionError,
    OcrCallable,
    extract,
    extract_html,
)
from moodle_llm_chat.ingest.model import html_to_text
from moodle_llm_chat.ingest.sources import (
    FetchedBytes,
    FetchedText,
    PermanentFetchError,
    TransientFetchError,
    fetch_external,
    host_of,
)
from moodle_llm_chat.llm.embed import CachingEmbedder

log = structlog.get_logger(__name__)

#: Bump when chunking or indexing changes; every document is then re-processed.
PIPELINE_VERSION = 1
CONTENT_VERSION = EXTRACT_VERSION * 100 + PIPELINE_VERSION

#: How long an idle stage loop sleeps before re-checking for due retries.
MAX_IDLE_WAIT_S = 30.0
MAX_RETRY_DELAY_S = 3600.0

ExternalFetch = Callable[..., Awaitable[FetchedText | FetchedBytes | None]]


class Outcome(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    WAITING = "waiting"


class OCRProvider(Protocol):
    def threadsafe(self, loop: asyncio.AbstractEventLoop) -> OcrCallable: ...


@dataclass(frozen=True)
class StageSpec:
    name: str
    max_attempts: int
    run: Callable[[Job], Awaitable[Outcome]]
    concurrency: Callable[[], int]


class _HostLimiter:
    """At most ``rate`` requests per second per host, spread evenly."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self._interval <= 0:
            return
        now = time.monotonic()
        due = max(self._next.get(host, now), now)
        self._next[host] = due + self._interval
        if due > now:
            await asyncio.sleep(due - now)


class Pipeline:
    def __init__(
        self,
        store: Store,
        *,
        embedder: CachingEmbedder | None,
        ocr: OCRProvider | None,
        http: httpx.AsyncClient,
        settings: PipelineSection,
        retry_base_s: float = 30.0,
        external_fetch: ExternalFetch | None = None,
        embed_batch: int = 64,
        clock: Callable[[], float] = time.time,
        ingest_gate: Callable[[str], bool] | None = None,
        ingest_meter: Callable[[str, int], None] | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.ocr = ocr
        self.http = http
        self.settings = settings
        self._retry_base = retry_base_s
        self._fetch = external_fetch or fetch_external
        self._embed_batch = embed_batch
        self._clock = clock
        self._hosts = _HostLimiter(settings.per_host_rate)
        self._stages = {
            spec.name: spec
            for spec in (
                StageSpec("fetch", 6, self._run_fetch, lambda: self.settings.fetch_concurrency),
                StageSpec("extract", 3, self._run_extract, lambda: self.settings.extract_workers),
                StageSpec("ocr", 5, self._run_ocr, lambda: self.settings.ocr_concurrency),
            )
        }
        self._wake = {name: asyncio.Event() for name in (*self._stages, "embed")}
        self._inflight: dict[str, set[asyncio.Task[None]]] = {n: set() for n in self._stages}
        self._loops: list[asyncio.Task[None]] = []
        self._embed_backoff = 0.0
        self._embed_failing = False
        self._poisoned: dict[int, int] = {}
        self._embedding_paused = False
        self._ingest_gate = ingest_gate
        self._ingest_meter = ingest_meter

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #

    def documents_changed(self, site_id: str, doc_ids: Sequence[str], *, bulk: bool) -> None:
        """Queue processing for changed documents (called by the ingest routes)."""
        priority = 1 if bulk else 0
        touched: set[str] = set()
        for doc_id in doc_ids:
            try:
                document = self.store.document(site_id, doc_id)
            except StoreError:
                continue
            stage = "fetch" if document.kind is DocumentKind.EXTERNAL else "extract"
            self.store.enqueue_job(site_id, doc_id, stage, priority=priority, now=self._now())
            touched.add(stage)
        for stage in touched:
            self._wake[stage].set()

    def blob_arrived(self, site_id: str, sha256: str) -> None:
        for doc_id in self.store.documents_with_blob(site_id, sha256):
            self.store.enqueue_job(site_id, doc_id, "extract", now=self._now())
        self._wake["extract"].set()

    def ingest_budget_changed(self) -> None:
        """Re-evaluate per-site ingest budgets (e.g. a ceiling was raised or a day began)."""
        self._wake["embed"].set()

    def _gated_sites(self) -> list[str]:
        if self._ingest_gate is None:
            return []
        return [s for s in self.store.sites_with_pending_vectors() if not self._ingest_gate(s)]

    def pause_embedding(self, paused: bool) -> None:
        """Used by ingest quotas: keep chunks pending instead of spending tokens."""
        self._embedding_paused = paused
        if not paused:
            self._wake["embed"].set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        requeued = self.store.reset_running_jobs()
        backlog = self.store.documents_needing_extraction(extract_version=CONTENT_VERSION)
        for d in backlog:
            stage = "fetch" if d.kind is DocumentKind.EXTERNAL else "extract"
            self.store.enqueue_job(d.site_id, d.doc_id, stage, priority=2, now=self._now())
        log.info("pipeline.start", requeued=requeued, backlog=len(backlog))
        for name in self._stages:
            self._loops.append(asyncio.create_task(self._stage_loop(name), name=f"stage:{name}"))
        if self.embedder is not None:
            self._loops.append(asyncio.create_task(self._embed_loop(), name="stage:embed"))

    async def stop(self) -> None:
        tasks = [*self._loops, *(t for s in self._inflight.values() for t in s)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loops.clear()
        for s in self._inflight.values():
            s.clear()

    async def idle(self, *, timeout: float, include_embeddings: bool = True) -> None:
        """Wait until no job is due or running (and, optionally, nothing to embed)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = any(self._inflight.values()) or self.store.has_due_jobs(now=self._now())
            if not busy and include_embeddings and self.embedder is not None:
                busy = self._embeddable_pending() > 0 and not self._embed_failing
            if not busy:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError("pipeline did not become idle")

    def status(self) -> dict[str, Any]:
        return {
            "stages": self.store.job_counts(now=self._now()),
            "pending_vectors": self.store.pending_vector_count(),
            "embedding_paused": self._embedding_paused,
            "embedding_failing": self._embed_failing,
        }

    # ------------------------------------------------------------------ #
    # Stage loops
    # ------------------------------------------------------------------ #

    def _now(self) -> int:
        return int(self._clock())

    async def _stage_loop(self, name: str) -> None:
        spec = self._stages[name]
        inflight = self._inflight[name]
        wake = self._wake[name]
        while True:
            free = spec.concurrency() - len(inflight)
            jobs = self.store.claim_jobs(name, limit=free, now=self._now()) if free > 0 else []
            for job in jobs:
                task = asyncio.create_task(self._run_job(spec, job))
                inflight.add(task)
                task.add_done_callback(self._job_finished(inflight, wake))
            if jobs and len(inflight) < spec.concurrency():
                continue
            wake.clear()
            due = self.store.next_job_due_at()
            wait = (
                MAX_IDLE_WAIT_S
                if due is None
                else max(0.0, min(MAX_IDLE_WAIT_S, due - self._clock()))
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=wait if wait > 0 else 0.05)

    @staticmethod
    def _job_finished(
        inflight: set[asyncio.Task[None]], wake: asyncio.Event
    ) -> Callable[[asyncio.Task[None]], None]:
        def done(task: asyncio.Task[None]) -> None:
            inflight.discard(task)
            wake.set()

        return done

    async def _run_job(self, spec: StageSpec, job: Job) -> None:
        try:
            outcome = await spec.run(job)
        except (TransientFetchError, httpx.TransportError) as exc:
            self._retry(spec, job, f"{spec.name}: {exc}")
        except (PermanentFetchError, ExtractionError) as exc:
            self._give_up(spec, job, f"{spec.name}: {exc}", state="skipped")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # unexpected: retry, then give up
            log.exception("pipeline.job_error", stage=spec.name, doc_id=job.doc_id)
            self._retry(spec, job, f"{spec.name}: {type(exc).__name__}: {exc}")
        else:
            self.store.finish_job(job.id, state=outcome.value, now=self._now())

    def _retry(self, spec: StageSpec, job: Job, error: str) -> None:
        delay = min(self._retry_base * (2**job.attempts), MAX_RETRY_DELAY_S)
        if not self.store.retry_job(
            job.id, delay_s=delay, error=error, max_attempts=spec.max_attempts, now=self._now()
        ):
            log.warning("pipeline.job_failed", stage=spec.name, doc_id=job.doc_id, error=error)
            self._mark_document(job, error)

    def _give_up(self, spec: StageSpec, job: Job, error: str, *, state: str) -> None:
        log.info("pipeline.job_skipped", stage=spec.name, doc_id=job.doc_id, error=error)
        self.store.finish_job(job.id, state=state, error=error, now=self._now())
        self._mark_document(job, error)

    def _mark_document(self, job: Job, reason: str) -> None:
        """Stop a hopeless document from blocking the backlog; keep the reason visible."""
        with contextlib.suppress(StoreError):
            self.store.record_extraction(
                job.site_id, job.doc_id, extract_version=CONTENT_VERSION, skip_reason=reason[:200]
            )

    # ------------------------------------------------------------------ #
    # Stages
    # ------------------------------------------------------------------ #

    def _live(self, job: Job) -> Document | None:
        try:
            document = self.store.document(job.site_id, job.doc_id)
        except StoreError:
            return None
        return None if document.tombstoned_at is not None else document

    async def _run_extract(self, job: Job) -> Outcome:
        document = self._live(job)
        if document is None:
            return Outcome.SKIPPED
        if document.kind is DocumentKind.INLINE:
            segments = [Segment(text=html_to_text(document.text or ""))]
        elif document.kind is DocumentKind.HTML:
            data = (document.text or "").encode("utf-8")
            segments = await asyncio.to_thread(extract_html, data)
        elif document.kind is DocumentKind.FILE:
            if not document.blob_sha256 or not self.store.blob_exists(document.blob_sha256):
                return Outcome.WAITING
            blob = self.store.read_blob(document.blob_sha256)
            try:
                segments = await asyncio.to_thread(
                    extract, blob, filename=document.filename or "file"
                )
            except ExtractionError as exc:
                raise ExtractionError(f"extract failed: {exc}") from exc
        else:  # external documents are handled by the fetch stage
            self.store.enqueue_job(job.site_id, job.doc_id, "fetch", now=self._now())
            self._wake["fetch"].set()
            return Outcome.SKIPPED
        self._index(document, segments, allow_ocr=True)
        return Outcome.DONE

    async def _run_fetch(self, job: Job) -> Outcome:
        document = self._live(job)
        if document is None or not document.external_url:
            return Outcome.SKIPPED
        await self._hosts.wait(host_of(document.external_url))
        result = await self._fetch(
            document.external_url,
            title=document.title,
            http=self.http,
            max_bytes=self.settings.max_file_bytes,
        )
        if result is None:
            self._index(document, [], allow_ocr=False, empty_reason="no-content")
            return Outcome.DONE
        if isinstance(result, FetchedText):
            segments = [Segment(text=result.text)]
        else:
            try:
                segments = await asyncio.to_thread(extract, result.data, filename=result.filename)
            except ExtractionError as exc:
                raise ExtractionError(f"extract failed: {exc}") from exc
        self._index(document, segments, allow_ocr=False)
        return Outcome.DONE

    async def _run_ocr(self, job: Job) -> Outcome:
        document = self._live(job)
        if document is None or self.ocr is None or not document.blob_sha256:
            return Outcome.SKIPPED
        if not self.store.blob_exists(document.blob_sha256):
            return Outcome.WAITING
        blob = self.store.read_blob(document.blob_sha256)
        ocr = self.ocr.threadsafe(asyncio.get_running_loop())
        segments = await asyncio.to_thread(
            extract, blob, filename=document.filename or "file", ocr=ocr
        )
        self._index(document, segments, allow_ocr=False)
        return Outcome.DONE

    def _index(
        self,
        document: Document,
        segments: list[Segment],
        *,
        allow_ocr: bool,
        empty_reason: str = "no-text",
    ) -> None:
        chunks = chunk_segments(segments, header_path=document.header_path)
        self.store.replace_content(
            document.site_id,
            document.doc_id,
            [
                SegmentIn(ordinal=i, text=s.text, page=s.page, label=s.label)
                for i, s in enumerate(segments)
            ],
            [
                ChunkIn(ordinal=c.ordinal, text=c.text, header_text=c.header_text, page=c.page)
                for c in chunks
            ],
        )
        needs_ocr = any(s.meta.get("needs_ocr") for s in segments) or (
            not chunks
            and document.kind is DocumentKind.FILE
            and (document.mimetype or "").startswith("image/")
        )
        skip_reason: str | None = None
        if needs_ocr and allow_ocr and self.ocr is not None:
            self.store.enqueue_job(document.site_id, document.doc_id, "ocr", now=self._now())
            self._wake["ocr"].set()
        elif needs_ocr:
            skip_reason = "needs-ocr"
        elif not chunks:
            skip_reason = empty_reason
        self.store.record_extraction(
            document.site_id,
            document.doc_id,
            extract_version=CONTENT_VERSION,
            skip_reason=skip_reason,
        )
        if chunks:
            self._wake["embed"].set()

    # ------------------------------------------------------------------ #
    # Embedding
    # ------------------------------------------------------------------ #

    def _embeddable_pending(self) -> int:
        return len(self.store.chunks_without_vectors(limit=1, exclude_ids=self._poisoned_ids()))

    def _poisoned_ids(self) -> set[int]:
        return {cid for cid, n in self._poisoned.items() if n >= 3}

    async def _embed_loop(self) -> None:
        assert self.embedder is not None
        wake = self._wake["embed"]
        while True:
            batch = (
                []
                if self._embedding_paused
                else self.store.chunks_without_vectors(
                    limit=self._embed_batch,
                    exclude_ids=self._poisoned_ids(),
                    exclude_sites=self._gated_sites(),
                )
            )
            if not batch:
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=MAX_IDLE_WAIT_S)
                continue
            try:
                vectors = await self.embedder.embed_documents(
                    [c.text for c in batch], skip_failures=True
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("pipeline.embed_error", error=type(exc).__name__)
                vectors = [None] * len(batch)
            stored = 0
            for chunk, vector in zip(batch, vectors, strict=True):
                if vector is None:
                    self._poisoned[chunk.chunk_id] = self._poisoned.get(chunk.chunk_id, 0) + 1
                    continue
                try:
                    self.store.set_embedding(chunk.chunk_id, vector)
                    stored += 1
                except Exception:  # chunk replaced while we were waiting on the provider
                    continue
                self._poisoned.pop(chunk.chunk_id, None)
                if self._ingest_meter is not None:
                    # Providers rarely report embedding usage: estimate ~4 chars/token.
                    self._ingest_meter(chunk.site_id, max(1, len(chunk.text) // 4))
            if stored == 0:
                # The provider is down or rejecting everything: back off, keep the work.
                self._embed_failing = True
                self._embed_backoff = min(max(self._embed_backoff * 2, 1.0), MAX_RETRY_DELAY_S)
                self._poisoned.clear()
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=self._embed_backoff)
            else:
                self._embed_failing = False
                self._embed_backoff = 0.0
