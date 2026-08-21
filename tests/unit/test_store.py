"""Verifies spec 004 — storage, manifest, blob cache, chunks and vectors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bsbot.index.store import SCHEMA_VERSION, Store, StoreVersionError
from bsbot.ingest.model import ContentItem, ContentKind, FileRef


def item(doc_id: str = "42:1:0", *, timemodified: int = 100, **kw) -> ContentItem:
    defaults = dict(
        doc_id=doc_id,
        course_id=42,
        course_name="LF05",
        section_name="Sec",
        module_id=1,
        module_name="Mod",
        modname="resource",
        title="Skript",
        kind=ContentKind.FILE,
        header_path=["LF05", "Sec", "Mod"],
        timemodified=timemodified,
        module_url="https://m.example/mod/resource/view.php?id=1",
        file=FileRef(
            url="https://m.example/f.pdf",
            filename="f.pdf",
            filesize=10,
            mimetype="application/pdf",
            timemodified=timemodified,
        ),
    )
    return ContentItem(**{**defaults, **kw})


@pytest.fixture
def store(tmp_path: Path):
    # vec0 fixes vector width at creation time, so tests that exercise embeddings
    # use a 2-dimensional store; production uses 768.
    with Store(tmp_path / "index.db", embed_dim=2) as s:
        yield s


class TestSchema:
    def test_open_creates_schema_and_is_idempotent(self, tmp_path: Path) -> None:
        """AC-1"""
        path = tmp_path / "index.db"
        with Store(path) as s:
            s.persist_crawl([item()])
        with Store(path) as s:
            assert len(s.active_documents()) == 1

    def test_newer_schema_is_refused(self, tmp_path: Path) -> None:
        """AC-2: better to refuse than to silently corrupt."""
        path = tmp_path / "index.db"
        with Store(path):
            pass
        con = sqlite3.connect(path)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.commit()
        con.close()
        with pytest.raises(StoreVersionError):
            Store(path).__enter__()

    def test_extensions_and_pragmas_are_active(self, store: Store) -> None:
        """AC-3 / AC-4: fail at startup, not mid-query."""
        assert store.connection.execute("select vec_version()").fetchone()[0]
        store.connection.execute("select * from chunks_fts limit 1")
        assert store.connection.execute("pragma journal_mode").fetchone()[0].lower() == "wal"
        assert store.connection.execute("pragma foreign_keys").fetchone()[0] == 1


class TestManifest:
    def test_crawl_is_persisted(self, store: Store) -> None:
        """AC-5"""
        store.persist_crawl([item("a"), item("b")])
        assert {d.doc_id for d in store.active_documents()} == {"a", "b"}

    def test_repersisting_updates_in_place(self, store: Store) -> None:
        """AC-6: no duplicates, and first_seen survives."""
        store.persist_crawl([item("a", timemodified=100)])
        first_seen = store.document("a").first_seen
        store.persist_crawl([item("a", timemodified=200)])
        docs = store.active_documents()
        assert len(docs) == 1
        assert docs[0].timemodified == 200
        assert store.document("a").first_seen == first_seen

    def test_absent_documents_are_tombstoned_not_deleted(self, store: Store) -> None:
        """AC-7"""
        store.persist_crawl([item("a"), item("b")])
        store.persist_crawl([item("a")])
        assert {d.doc_id for d in store.active_documents()} == {"a"}
        assert store.document("b").tombstoned_at is not None

    def test_reappearing_document_is_revived(self, store: Store) -> None:
        """AC-8"""
        store.persist_crawl([item("a"), item("b")])
        store.persist_crawl([item("a")])
        store.persist_crawl([item("a"), item("b")])
        assert {d.doc_id for d in store.active_documents()} == {"a", "b"}
        assert store.document("b").tombstoned_at is None

    def test_pending_extraction_covers_new_changed_and_stale_version(self, store: Store) -> None:
        """AC-9: the three reasons a document needs re-extraction."""
        store.persist_crawl([item("new"), item("changed"), item("stale"), item("current")])
        for doc_id in ("changed", "stale", "current"):
            store.record_extraction(doc_id, text_sha256="x", extract_version=1)
        store.persist_crawl(
            [item("new"), item("changed", timemodified=999), item("stale"), item("current")]
        )
        pending = {d.doc_id for d in store.documents_needing_extraction(extract_version=1)}
        assert pending == {"new", "changed"}
        stale = {d.doc_id for d in store.documents_needing_extraction(extract_version=2)}
        assert "stale" in stale and "current" in stale

    def test_inline_text_is_round_tripped(self, store: Store) -> None:
        """Inline label text needs no fetching, so it must survive persistence."""
        store.persist_crawl(
            [item("i", kind=ContentKind.INLINE, file=None, text="Klausur am 15.03.2026")]
        )
        assert store.document("i").text == "Klausur am 15.03.2026"


class TestBlobCache:
    def test_blob_is_content_addressed_and_shared(self, store: Store, tmp_path: Path) -> None:
        """AC-10: two modules linking the same PDF share one copy on disk."""
        digest = store.put_blob(b"hello world")
        again = store.put_blob(b"hello world")
        assert digest == again
        assert store.blob_path(digest).parts[-2] == digest[:2]
        assert store.blob_path(digest).read_bytes() == b"hello world"
        assert store.blob_count() == 1

    def test_fetch_metadata_round_trips(self, store: Store) -> None:
        """AC-11"""
        store.record_fetch(
            "https://m.example/f.pdf",
            sha256="abc",
            etag='W/"1"',
            last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
            moodle_timemodified=123,
            fetched_at=1000,
            checked_at=1000,
        )
        rec = store.fetch_record("https://m.example/f.pdf")
        assert rec is not None
        assert rec.etag == 'W/"1"' and rec.sha256 == "abc" and rec.moodle_timemodified == 123

    def test_failed_fetch_is_remembered(self, store: Store) -> None:
        """AC-12: don't retry a 403 on every single sync."""
        store.record_fetch_failure("https://m.example/gone.pdf", error="HTTP 404", checked_at=5)
        rec = store.fetch_record("https://m.example/gone.pdf")
        assert rec is not None and rec.error == "HTTP 404" and rec.sha256 is None

    def test_unreferenced_blobs_are_reported_not_deleted(self, store: Store) -> None:
        """AC-13: pruning must be explicit."""
        keep = store.put_blob(b"keep")
        orphan = store.put_blob(b"orphan")
        store.persist_crawl([item("a")])
        store.record_extraction("a", text_sha256="t", extract_version=1, blob_sha256=keep)
        assert set(store.unreferenced_blobs()) == {orphan}
        assert store.blob_path(orphan).exists()


class TestChunks:
    def test_chunks_are_replaced_atomically(self, store: Store) -> None:
        """AC-14: never a window with both old and new chunks visible."""
        store.persist_crawl([item("a")])
        store.replace_chunks("a", [("erste fassung", {"ordinal": 0})])
        store.replace_chunks("a", [("zweite fassung", {"ordinal": 0}), ("mehr", {"ordinal": 1})])
        texts = [c.text for c in store.chunks_for("a")]
        assert texts == ["zweite fassung", "mehr"]

    def test_tombstoning_removes_chunks_from_every_index(self, store: Store) -> None:
        """AC-15: chunks, FTS and vectors must go together."""
        store.persist_crawl([item("a")])
        store.replace_chunks("a", [("Netzwerkgrundlagen", {"ordinal": 0})])
        chunk_id = store.chunks_for("a")[0].chunk_id
        store.set_embedding(chunk_id, [0.6, 0.8])

        store.persist_crawl([])  # 'a' disappears -> tombstoned

        assert store.chunks_for("a") == []
        con = store.connection
        assert con.execute("select count(*) from chunks_fts").fetchone()[0] == 0
        assert con.execute("select count(*) from chunks_vec").fetchone()[0] == 0

    def test_fts_index_is_populated(self, store: Store) -> None:
        store.persist_crawl([item("a")])
        store.replace_chunks("a", [("Die Abschlussprüfung ist im März", {"ordinal": 0})])
        hits = store.connection.execute(
            "select count(*) from chunks_fts where chunks_fts match ?", ("Abschlussprüfung",)
        ).fetchone()[0]
        assert hits == 1


class TestEmbeddings:
    def test_embedding_cache_is_keyed_by_text_and_model(self, store: Store) -> None:
        """AC-16: re-chunking must not re-pay for unchanged text."""
        store.cache_embedding(
            "sha-1", "gemini-embedding-001", 768, "RETRIEVAL_DOCUMENT", [1.0, 0.0]
        )
        assert store.cached_embedding("sha-1", "gemini-embedding-001", 768, "RETRIEVAL_DOCUMENT")
        assert (
            store.cached_embedding("sha-1", "gemini-embedding-001", 768, "RETRIEVAL_QUERY") is None
        )
        assert store.cached_embedding("sha-1", "other-model", 768, "RETRIEVAL_DOCUMENT") is None

    def test_vectors_are_normalised_on_write(self, store: Store) -> None:
        """AC-17: Gemini returns un-normalised vectors at 768 dims (norm ~0.58).

        Storing them raw would make cosine ranking subtly wrong, which is the kind
        of bug that degrades answers without ever raising an error.
        """
        store.persist_crawl([item("a")])
        store.replace_chunks("a", [("text", {"ordinal": 0})])
        chunk_id = store.chunks_for("a")[0].chunk_id
        store.set_embedding(chunk_id, [3.0, 4.0])  # norm 5.0

        stored = store.embedding_for(chunk_id)
        assert stored is not None
        assert pytest.approx(sum(v * v for v in stored), abs=1e-6) == 1.0
        assert pytest.approx(stored[0], abs=1e-6) == 0.6


class TestOcrCache:
    def test_transcription_is_cached_per_image(self, store: Store) -> None:
        """Spec 006 AC-11: a scanned page is paid for exactly once."""
        assert store.cached_ocr("img-1", "ocr-v1") is None
        store.cache_ocr("img-1", "ocr-v1", "Aufgabe 1: Berechnen Sie ...")
        assert store.cached_ocr("img-1", "ocr-v1") == "Aufgabe 1: Berechnen Sie ..."
        assert store.cached_ocr("img-1", "ocr-v2") is None


class TestExtractionReset:
    def test_documents_with_no_chunks_can_be_retried(self, store: Store) -> None:
        """Needed when a capability arrives later (OCR) that would now succeed.

        Without this, 70 scanned PDFs stay permanently marked 'extracted' with zero
        text, and enabling OCR would require re-processing the entire corpus.
        """
        store.persist_crawl([item("empty"), item("full")])
        for doc_id in ("empty", "full"):
            store.record_extraction(doc_id, text_sha256="x", extract_version=2)
        store.replace_chunks("full", [("hat text", {"ordinal": 0})])

        assert store.documents_needing_extraction(extract_version=2) == []
        reset = store.reset_extraction_for_empty_documents()

        assert reset == 1
        pending = {d.doc_id for d in store.documents_needing_extraction(extract_version=2)}
        assert pending == {"empty"}


class TestTargetedExtractionReset:
    """Spec 007 AC-22: editing the alias file must not force a full re-index."""

    def test_only_named_documents_are_reset(self, store: Store) -> None:
        store.persist_crawl([item("a"), item("b"), item("c")])
        for doc_id in ("a", "b", "c"):
            store.record_extraction(doc_id, text_sha256="x", extract_version=1)
        assert store.documents_needing_extraction(extract_version=1) == []

        reset = store.reset_extraction_for_doc_ids(["a", "c"])

        assert reset == 2
        pending = {d.doc_id for d in store.documents_needing_extraction(extract_version=1)}
        assert pending == {"a", "c"}

    def test_unknown_doc_ids_are_silently_ignored(self, store: Store) -> None:
        store.persist_crawl([item("a")])
        assert store.reset_extraction_for_doc_ids(["does-not-exist"]) == 0
