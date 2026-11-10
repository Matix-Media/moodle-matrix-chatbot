"""Processing pipeline: persisted jobs → fetch / extract / ocr stages → embedding."""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from factories import doc, file_doc
from langchain_core.embeddings import Embeddings

from moodle_llm_chat.config import PipelineSection
from moodle_llm_chat.index.models import DocumentKind
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.ingest.pipeline import CONTENT_VERSION, Pipeline
from moodle_llm_chat.ingest.sources import FetchedText, TransientFetchError
from moodle_llm_chat.llm.embed import CachingEmbedder

S = "site-a"


def pdf_bytes(pages: list[str]) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return bytes(data)


class Vectors(Embeddings):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("503 UNAVAILABLE")
        return [[float(len(t)), 1.0, 0.5] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class Cache:
    def __init__(self) -> None:
        self.d: dict[tuple[str, str], list[float]] = {}

    def cached_embedding(self, h: str, k: str) -> list[float] | None:
        return self.d.get((h, k))

    def cache_embedding(self, h: str, k: str, v: list[float]) -> None:
        self.d[(h, k)] = v


class FakeOCR:
    def __init__(self) -> None:
        self.calls = 0

    def threadsafe(self, loop: asyncio.AbstractEventLoop) -> Any:
        def call(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            self.calls += 1
            return f"OCR-Text der gescannten Seite {page} mit genug Zeichen für den Index."

        return call


def make_embedder(vectors: Vectors) -> CachingEmbedder:
    async def no_sleep(_: float) -> None:
        return None

    return CachingEmbedder(
        query_api=vectors,
        document_api=vectors,
        cache=Cache(),
        provider="fake",
        model="m",
        dim=3,
        rpm=0,
        items_per_minute=0,
        max_retries=0,
        sleep=no_sleep,
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


async def running(pipeline: Pipeline) -> Pipeline:
    await pipeline.start()
    return pipeline


def make(
    store: Store,
    http: httpx.AsyncClient,
    *,
    vectors: Vectors | None = None,
    ocr: Any = None,
    fetcher: Any = None,
    **kw: Any,
) -> Pipeline:
    return Pipeline(
        store,
        embedder=make_embedder(vectors or Vectors()),
        ocr=ocr,
        http=http,
        settings=PipelineSection(),
        retry_base_s=0,
        external_fetch=fetcher,
        **kw,
    )


class TestInlineAndHtml:
    async def test_inline_document_is_chunked_and_embedded(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        store.upsert_documents(S, [doc(text="Die Prüfung ist am Montag in Raum 204.")])
        p = await running(make(store, http))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        segs = store.segments(S, "2:10:intro")
        assert segs[0].text.startswith("Die Prüfung")
        assert store.chunk_count(S) == 1
        assert store.pending_vector_count() == 0
        assert store.document(S, "2:10:intro").extract_version == CONTENT_VERSION
        assert store.fts_chunk_ids(S, "Raum", limit=3)

    async def test_html_document_drops_markup(self, store: Store, http: httpx.AsyncClient) -> None:
        store.upsert_documents(
            S,
            [
                doc(
                    kind=DocumentKind.HTML,
                    text="<h1>Stundenplan</h1><script>x()</script><p>Mo 8 Uhr</p>",
                )
            ],
        )
        p = await running(make(store, http))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        text = store.segments(S, "2:10:intro")[0].text
        assert "Stundenplan" in text and "Mo 8 Uhr" in text
        assert "script" not in text and "x()" not in text


class TestFiles:
    async def test_file_waits_for_its_blob_then_processes(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        data = pdf_bytes(["Subnetting erklaert auf Seite eins mit vielen Woertern fuer den Test."])
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(S, [file_doc("2:20:pdf", sha)])
        p = await running(make(store, http))
        p.documents_changed(S, ["2:20:pdf"], bulk=False)
        await p.idle(timeout=5)
        assert store.chunk_count(S) == 0
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        p.blob_arrived(S, sha)
        await p.idle(timeout=5)
        await p.stop()
        (seg,) = store.segments(S, "2:20:pdf")
        assert seg.page == 1
        assert "Subnetting" in seg.text

    async def test_corrupt_file_is_skipped_not_retried(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        data = b"%PDF-1.4 this is not really a pdf"
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(S, [file_doc("2:20:pdf", sha)])
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        p = await running(make(store, http))
        p.documents_changed(S, ["2:20:pdf"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        d = store.document(S, "2:20:pdf")
        assert d.extract_version == CONTENT_VERSION
        assert d.skip_reason and "extract" in d.skip_reason
        assert store.job_counts()["extract"].get("failed", 0) == 0

    async def test_scanned_page_goes_to_ocr(self, store: Store, http: httpx.AsyncClient) -> None:
        data = pdf_bytes(["Normale Textseite. " * 8, ""])
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(S, [file_doc("2:20:pdf", sha)])
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        ocr = FakeOCR()
        p = await running(make(store, http, ocr=ocr))
        p.documents_changed(S, ["2:20:pdf"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        pages = {s.page: s.text for s in store.segments(S, "2:20:pdf")}
        assert ocr.calls == 1
        assert "OCR-Text" in pages[2]
        assert store.document(S, "2:20:pdf").skip_reason is None

    async def test_scanned_page_without_ocr_is_marked(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        data = pdf_bytes(["", ""])
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(S, [file_doc("2:20:pdf", sha)])
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        p = await running(make(store, http))
        p.documents_changed(S, ["2:20:pdf"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        assert store.document(S, "2:20:pdf").skip_reason == "needs-ocr"


class TestExternal:
    async def test_external_text_is_indexed(self, store: Store, http: httpx.AsyncClient) -> None:
        async def fetcher(url: str, **kw: Any) -> FetchedText:
            return FetchedText(text="Transkript des Erklärvideos zu VLANs.")

        store.upsert_documents(
            S, [doc(kind=DocumentKind.EXTERNAL, text=None, external_url="https://youtu.be/x")]
        )
        p = await running(make(store, http, fetcher=fetcher))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        assert "VLANs" in store.segments(S, "2:10:intro")[0].text

    async def test_transient_failure_is_retried(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        attempts = 0

        async def flaky(url: str, **kw: Any) -> FetchedText:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TransientFetchError("503")
            return FetchedText(text="Endlich da.")

        store.upsert_documents(
            S, [doc(kind=DocumentKind.EXTERNAL, text=None, external_url="https://hackmd.io/x")]
        )
        p = await running(make(store, http, fetcher=flaky))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        assert attempts == 3
        assert store.segments(S, "2:10:intro")[0].text == "Endlich da."

    async def test_exhausted_retries_mark_document_skipped(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        async def dead(url: str, **kw: Any) -> FetchedText:
            raise TransientFetchError("503")

        store.upsert_documents(
            S, [doc(kind=DocumentKind.EXTERNAL, text=None, external_url="https://hackmd.io/x")]
        )
        p = await running(make(store, http, fetcher=dead))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5)
        await p.stop()
        assert store.job_counts()["fetch"]["failed"] == 1
        d = store.document(S, "2:10:intro")
        assert d.skip_reason and "fetch" in d.skip_reason
        assert not d.needs_extraction


class TestIsolationAndRecovery:
    async def test_embedding_outage_does_not_block_extraction(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        store.upsert_documents(S, [doc()])
        p = await running(make(store, http, vectors=Vectors(fail=True)))
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        await p.idle(timeout=5, include_embeddings=False)
        await p.stop()
        assert store.chunk_count(S) == 1
        assert store.pending_vector_count() == 1

    async def test_slow_stage_does_not_delay_another(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        release = asyncio.Event()

        async def blocked(url: str, **kw: Any) -> FetchedText:
            await release.wait()
            return FetchedText(text="spät")

        store.upsert_documents(
            S,
            [
                doc(
                    "2:10:ext",
                    kind=DocumentKind.EXTERNAL,
                    text=None,
                    external_url="https://hackmd.io/x",
                ),
                doc("2:11:inline"),
            ],
        )
        p = await running(make(store, http, fetcher=blocked))
        p.documents_changed(S, ["2:10:ext", "2:11:inline"], bulk=False)
        for _ in range(100):
            if store.segments(S, "2:11:inline"):
                break
            await asyncio.sleep(0.01)
        assert store.segments(S, "2:11:inline")
        release.set()
        await p.idle(timeout=5)
        await p.stop()

    async def test_startup_resumes_backlog_and_interrupted_jobs(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        store.upsert_documents(S, [doc("2:10:a"), doc("2:11:b")])
        store.enqueue_job(S, "2:10:a", "extract")
        store.claim_jobs("extract", limit=1)  # a crash left it "running"
        p = await running(make(store, http))
        await p.idle(timeout=5)
        await p.stop()
        assert store.segments(S, "2:10:a") and store.segments(S, "2:11:b")

    async def test_concurrency_limit_is_respected(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        inflight = 0
        peak = 0

        async def slow(url: str, **kw: Any) -> FetchedText:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.02)
            inflight -= 1
            return FetchedText(text="ok")

        docs = [
            doc(
                f"2:{i}:x",
                kind=DocumentKind.EXTERNAL,
                text=None,
                external_url=f"https://h{i}.test/s/abcd",
            )
            for i in range(6)
        ]
        store.upsert_documents(S, docs)
        p = await running(make(store, http, fetcher=slow))
        p.settings = PipelineSection(fetch_concurrency=2)
        p.documents_changed(S, [d.doc_id for d in docs], bulk=True)
        await p.idle(timeout=5)
        await p.stop()
        assert peak <= 2

    async def test_status_reports_stages_and_vectors(
        self, store: Store, http: httpx.AsyncClient
    ) -> None:
        store.upsert_documents(S, [doc()])
        p = make(store, http)
        p.documents_changed(S, ["2:10:intro"], bulk=False)
        status = p.status()
        assert status["stages"]["extract"]["queued"] == 1
        assert "pending_vectors" in status
