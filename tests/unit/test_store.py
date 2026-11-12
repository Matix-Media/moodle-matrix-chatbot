"""Store v2: encrypted, site-scoped storage of courses, documents, blobs and chunks."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from factories import doc, file_doc, read_disk

from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.db import DatabaseKeyError
from moodle_llm_chat.index.models import ChunkIn, CourseIn, SegmentIn
from moodle_llm_chat.index.store import Store, StoreError, SyncStateError

SITE = "site-a"
OTHER = "site-b"


def _course(course_id: int = 2, **kw: object) -> CourseIn:
    base: dict[str, object] = {
        "course_id": course_id,
        "fullname": "Netzwerke LF7",
        "shortname": "LF7",
        "visible": True,
        "startdate": 0,
        "enddate": 0,
        "timemodified": 1_700_000_000,
    }
    base.update(kw)
    return CourseIn(**base)  # type: ignore[arg-type]


class TestSites:
    def test_add_returns_secret_once_and_authenticates_it(self, store: Store) -> None:
        secret = store.add_site(SITE, name="Schule A", url="https://moodle.a.test")
        assert len(secret) >= 40
        site = store.authenticate_site(secret)
        assert site is not None and site.site_id == SITE
        assert store.authenticate_site("wrong") is None

    def test_secret_is_retrievable_for_callbacks_but_not_stored_in_clear(
        self, store: Store, tmp_path: Path
    ) -> None:
        secret = store.add_site(SITE, name="A", url="https://moodle.a.test")
        assert store.site_secret(SITE) == secret
        store.close()
        assert secret.encode() not in read_disk(tmp_path / "index.db")

    def test_revoked_site_no_longer_authenticates(self, store: Store) -> None:
        secret = store.add_site(SITE, name="A", url="https://moodle.a.test")
        store.revoke_site(SITE)
        assert store.authenticate_site(secret) is None

    def test_rotation_keeps_old_secret_during_grace(self, store: Store) -> None:
        old = store.add_site(SITE, name="A", url="https://moodle.a.test")
        new = store.rotate_site_secret(SITE, grace_s=3600, now=1000)
        assert new != old
        assert store.authenticate_site(new, now=1001) is not None
        assert store.authenticate_site(old, now=1001) is not None
        assert store.authenticate_site(old, now=1000 + 3601) is None
        assert store.site_secret(SITE) == new
        assert store.site_secrets(SITE, now=1001) == [new, old]

    def test_duplicate_site_rejected(self, store: Store) -> None:
        store.add_site(SITE, name="A", url="https://moodle.a.test")
        with pytest.raises(StoreError):
            store.add_site(SITE, name="A", url="https://moodle.a.test")

    def test_list_sites(self, store: Store) -> None:
        store.add_site(SITE, name="A", url="https://moodle.a.test")
        store.add_site(OTHER, name="B", url="https://moodle.b.test")
        assert [s.site_id for s in store.sites()] == [SITE, OTHER]


class TestDocuments:
    def test_upsert_and_read_back_decrypted(self, store: Store) -> None:
        store.upsert_courses(SITE, [_course()])
        changed = store.upsert_documents(SITE, [doc()])
        assert changed == ["2:10:intro"]
        got = store.document(SITE, "2:10:intro")
        assert got.title == "Einführung"
        assert got.text == "Willkommen im Kurs Netzwerke."
        assert got.header_path == ["Netzwerke", "Woche 1", "Einführung"]
        assert got.needs_extraction

    def test_unchanged_content_hash_is_skipped(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        store.record_extraction(SITE, "2:10:intro", extract_version=1)
        assert store.upsert_documents(SITE, [doc()]) == []
        assert not store.document(SITE, "2:10:intro").needs_extraction

    def test_moved_document_takes_its_new_placement_without_reextraction(
        self, store: Store
    ) -> None:
        """A section inserted above shifts this one down; only its position changed."""
        store.upsert_documents(SITE, [doc(section_id=83, section_order=2)])
        store.record_extraction(SITE, "2:10:intro", extract_version=1)
        result = store.upsert_documents_detailed(
            SITE, [doc(section_id=83, section_order=3, placement_hash="p2")]
        )
        assert result.changed == [] and result.relocated == []
        d = store.document(SITE, "2:10:intro")
        assert (d.section_id, d.section_order) == (83, 3)
        assert not d.needs_extraction

    def test_renamed_section_relocates_without_reextraction(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        store.record_extraction(SITE, "2:10:intro", extract_version=1)
        renamed = doc(
            section_name="Woche 1 (neu)", header_path=["Netzwerke", "Woche 1 (neu)", "Einführung"]
        )
        result = store.upsert_documents_detailed(SITE, [renamed])
        assert result.changed == [] and result.relocated == ["2:10:intro"]
        d = store.document(SITE, "2:10:intro")
        assert d.section_name == "Woche 1 (neu)"
        assert d.header_path == ["Netzwerke", "Woche 1 (neu)", "Einführung"]
        assert not d.needs_extraction

    def test_changed_content_requires_reextraction(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        store.record_extraction(SITE, "2:10:intro", extract_version=1)
        assert store.upsert_documents(SITE, [doc(text="Neu", content_hash="h2")]) == ["2:10:intro"]
        assert store.document(SITE, "2:10:intro").needs_extraction

    def test_sites_are_isolated(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        with pytest.raises(StoreError):
            store.document(OTHER, "2:10:intro")

    def test_payload_is_not_on_disk_in_clear(self, store: Store, tmp_path: Path) -> None:
        store.upsert_courses(SITE, [_course(fullname="Geheimkurs Sicherheit")])
        store.upsert_documents(SITE, [doc(text="Klausur-Lösung: 42 geheim")])
        store.close()
        disk = read_disk(tmp_path / "index.db")
        assert b"Klausur" not in disk
        assert b"Geheimkurs" not in disk

    def test_tombstone_removes_chunks_and_hides_document(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        store.replace_content(
            SITE,
            "2:10:intro",
            [SegmentIn(ordinal=0, text="Willkommen")],
            [ChunkIn(ordinal=0, text="Willkommen", header_text="Netzwerke")],
        )
        store.tombstone_documents(SITE, ["2:10:intro"])
        assert store.live_documents(SITE) == []
        assert store.chunk_count(SITE) == 0

    def test_documents_needing_extraction(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a"), doc("2:11:b")])
        store.record_extraction(SITE, "2:10:a", extract_version=1)
        assert [d.doc_id for d in store.documents_needing_extraction(extract_version=1)] == [
            "2:11:b"
        ]
        assert len(store.documents_needing_extraction(extract_version=2)) == 2

    def test_courses_upsert_and_tombstone(self, store: Store) -> None:
        store.upsert_courses(SITE, [_course(2), _course(3, fullname="Mathe")])
        store.upsert_documents(SITE, [doc("3:5:x")])
        store.tombstone_courses(SITE, [3])
        assert [c.course_id for c in store.courses(SITE)] == [2]
        assert store.live_documents(SITE) == []


class TestSyncRuns:
    def test_full_sync_tombstones_unseen_documents(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a"), doc("2:11:b")], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        store.upsert_documents(SITE, [doc("2:10:a", content_hash="new")], now=110)
        result = store.finish_sync(SITE, "s1", now=200)
        assert result.tombstoned == ["2:11:b"]
        assert [d.doc_id for d in store.live_documents(SITE)] == ["2:10:a"]

    def test_manifest_marks_unchanged_as_seen_and_reports_needed(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a"), doc("2:11:b")], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        need = store.apply_manifest(
            SITE,
            "s1",
            {"2:10:a": "h-2:10:a", "2:11:b": "changed", "2:12:new": "h-new"},
            now=110,
        )
        assert need == ["2:11:b", "2:12:new"]
        store.upsert_documents(SITE, [doc("2:11:b", content_hash="changed")], now=115)
        result = store.finish_sync(SITE, "s1", now=120)
        assert result.tombstoned == []

    def test_manifest_requests_documents_whose_placement_changed(self, store: Store) -> None:
        store.upsert_documents(
            SITE,
            [doc("2:10:a", placement_hash="p1"), doc("2:11:b", placement_hash="p1"), doc("2:12:c")],
        )
        store.start_sync(SITE, "s1", mode="full", now=100)
        need = store.apply_manifest(
            SITE,
            "s1",
            {
                "2:10:a": ("h-2:10:a", "p1"),  # unchanged
                "2:11:b": ("h-2:11:b", "p2"),  # moved
                "2:12:c": ("h-2:12:c", "p1"),  # stored before placement hashes existed
            },
            now=110,
        )
        assert need == ["2:11:b", "2:12:c"]

    def test_manifest_without_placement_hash_compares_content_only(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a", placement_hash="p1")])
        store.start_sync(SITE, "s1", mode="full", now=100)
        assert store.apply_manifest(SITE, "s1", {"2:10:a": ("h-2:10:a", None)}, now=110) == []

    def test_documents_pushed_during_the_run_survive_without_sync_id(self, store: Store) -> None:
        """The plugin sends no sync_id with documents; activity during the run counts."""
        store.upsert_documents(SITE, [doc("2:10:old"), doc("2:11:changed")], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        store.apply_manifest(SITE, "s1", {"2:11:changed": "h2", "2:12:new": "h3"}, now=105)
        store.upsert_documents(
            SITE,
            [doc("2:11:changed", content_hash="h2"), doc("2:12:new", content_hash="h3")],
            now=110,
        )
        result = store.finish_sync(SITE, "s1", now=120)
        assert result.tombstoned == ["2:10:old"]
        assert {d.doc_id for d in store.live_documents(SITE)} == {"2:11:changed", "2:12:new"}

    def test_listed_document_the_plugin_failed_to_send_is_kept(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a")], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        assert store.apply_manifest(SITE, "s1", {"2:10:a": "changed"}, now=105) == ["2:10:a"]
        assert store.finish_sync(SITE, "s1", now=120).tombstoned == []

    def test_stale_run_never_tombstones(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a")], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        with pytest.raises(SyncStateError, match="stale"):
            store.finish_sync(SITE, "s1", now=100 + 25 * 3600)
        assert [d.doc_id for d in store.live_documents(SITE)] == ["2:10:a"]

    def test_incremental_finish_does_not_tombstone(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:10:a")], now=50)
        store.start_sync(SITE, "s1", mode="incremental", now=100)
        assert store.finish_sync(SITE, "s1", now=101).tombstoned == []

    def test_unknown_sync_rejected(self, store: Store) -> None:
        with pytest.raises(SyncStateError):
            store.finish_sync(SITE, "nope", now=1)

    def test_full_sync_tombstones_unseen_courses(self, store: Store) -> None:
        store.upsert_courses(SITE, [_course(2), _course(3)], now=50)
        store.start_sync(SITE, "s1", mode="full", now=100)
        store.upsert_courses(SITE, [_course(2)], now=105)  # no sync_id, like the plugin
        store.finish_sync(SITE, "s1", now=110)
        assert [c.course_id for c in store.courses(SITE)] == [2]


class TestBlobs:
    def test_needs_blob_until_uploaded(self, store: Store) -> None:
        data = b"%PDF-1.4 geheimer inhalt"
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(SITE, [file_doc("2:20:file", sha)])
        assert store.missing_blobs([sha]) == [sha]
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        assert store.missing_blobs([sha]) == []
        assert store.read_blob(sha) == data

    def test_blob_is_encrypted_on_disk(self, store: Store, tmp_path: Path) -> None:
        data = b"Lehrer-Musterloesung vertraulich"
        sha = hashlib.sha256(data).hexdigest()
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        assert b"Musterloesung" not in read_disk(tmp_path / "index.db")

    def test_digest_mismatch_is_rejected_and_leaves_nothing(self, store: Store) -> None:
        with pytest.raises(StoreError, match="digest"):
            store.put_blob(io.BytesIO(b"abc"), expected_sha256="0" * 64)
        assert store.missing_blobs(["0" * 64]) == ["0" * 64]
        assert list(store.blobs_dir.rglob("*.tmp")) == []

    def test_size_limit(self, store: Store) -> None:
        data = b"x" * 100
        with pytest.raises(StoreError, match="large"):
            store.put_blob(
                io.BytesIO(data), expected_sha256=hashlib.sha256(data).hexdigest(), max_bytes=10
            )

    def test_reupload_is_a_noop(self, store: Store) -> None:
        data = b"same"
        sha = hashlib.sha256(data).hexdigest()
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        assert store.read_blob(sha) == data


class TestContent:
    def _index(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc()])
        store.replace_content(
            SITE,
            "2:10:intro",
            [
                SegmentIn(ordinal=0, page=1, text="Seite eins über Subnetze."),
                SegmentIn(ordinal=1, page=2, text="Seite zwei über Routing."),
            ],
            [
                ChunkIn(ordinal=0, page=1, text="Subnetze und Prüfungen", header_text="LF7"),
                ChunkIn(ordinal=1, page=2, text="Routing Tabellen", header_text="LF7"),
            ],
        )

    def test_segments_round_trip(self, store: Store) -> None:
        self._index(store)
        segs = store.segments(SITE, "2:10:intro")
        assert [(s.page, s.text) for s in segs] == [
            (1, "Seite eins über Subnetze."),
            (2, "Seite zwei über Routing."),
        ]

    def test_fts_finds_words_with_diacritic_folding(self, store: Store) -> None:
        self._index(store)
        ids = store.fts_chunk_ids(SITE, '"prufungen"', limit=5)
        assert len(ids) == 1
        (chunk,) = store.chunks(SITE, ids)
        assert chunk.text == "Subnetze und Prüfungen"
        assert chunk.doc_id == "2:10:intro"

    def test_fts_is_site_scoped(self, store: Store) -> None:
        self._index(store)
        assert store.fts_chunk_ids(OTHER, "Routing", limit=5) == []

    def test_replace_content_is_atomic_swap(self, store: Store) -> None:
        self._index(store)
        store.replace_content(
            SITE, "2:10:intro", [SegmentIn(ordinal=0, text="neu")], [ChunkIn(ordinal=0, text="neu")]
        )
        assert store.chunk_count(SITE) == 1
        assert store.fts_chunk_ids(SITE, "Routing", limit=5) == []

    def test_chunk_text_not_on_disk_in_clear(self, store: Store, tmp_path: Path) -> None:
        self._index(store)
        store.close()
        assert b"Subnetze" not in read_disk(tmp_path / "index.db")

    def test_embeddings_and_pending(self, store: Store) -> None:
        self._index(store)
        pending = store.chunks_without_vectors(limit=10)
        assert len(pending) == 2
        store.set_embedding(pending[0].chunk_id, [1.0, 0.0, 0.0])
        assert len(store.chunks_without_vectors(limit=10)) == 1
        hits = store.vector_chunk_ids(SITE, [1.0, 0.0, 0.0], limit=1)
        assert hits == [pending[0].chunk_id]


class TestCaches:
    def test_embedding_cache_round_trip(self, store: Store) -> None:
        store.cache_embedding("a" * 64, "p:m:3:document", [0.1, 0.2, 0.3])
        got = store.cached_embedding("a" * 64, "p:m:3:document")
        assert got == pytest.approx([0.1, 0.2, 0.3])
        assert store.cached_embedding("a" * 64, "p:m:3:query") is None

    def test_ocr_cache_round_trip_and_encrypted(self, store: Store, tmp_path: Path) -> None:
        store.cache_ocr("b" * 64, "tag", "Transkript geheim")
        assert store.cached_ocr("b" * 64, "tag") == "Transkript geheim"
        store.close()
        assert b"Transkript" not in read_disk(tmp_path / "index.db")


class TestReopen:
    def test_data_survives_reopen_with_same_key(self, tmp_path: Path, ring: KeyRing) -> None:
        with Store(tmp_path / "x.db", ring, embed_dim=3) as s:
            secret = s.add_site(SITE, name="A", url="https://moodle.a.test")
            s.upsert_documents(SITE, [doc()])
        with Store(tmp_path / "x.db", KeyRing(ring_master(ring)), embed_dim=3) as s:
            assert s.authenticate_site(secret) is not None
            assert s.document(SITE, "2:10:intro").text == "Willkommen im Kurs Netzwerke."

    def test_embedding_dim_change_is_refused(self, tmp_path: Path, ring: KeyRing) -> None:
        with Store(tmp_path / "x.db", ring, embed_dim=3):
            pass
        with (
            pytest.raises(StoreError, match="dimension"),
            Store(tmp_path / "x.db", ring, embed_dim=4),
        ):
            pass

    def test_v1_index_gains_placement_hash(self, tmp_path: Path, ring: KeyRing) -> None:
        with Store(tmp_path / "x.db", ring, embed_dim=3) as s:
            s.upsert_documents(SITE, [doc()])
            con = s.connection
            con.execute("ALTER TABLE documents DROP COLUMN placement_hash")
            con.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
            con.commit()
        with Store(tmp_path / "x.db", ring, embed_dim=3) as s:
            s.upsert_documents(SITE, [doc(placement_hash="p1")])
            s.start_sync(SITE, "s1", mode="full", now=100)
            assert s.apply_manifest(SITE, "s1", {"2:10:intro": ("h-2:10:intro", "p1")}) == []
            version = s.connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            assert version == "2"


def ring_master(ring: KeyRing) -> bytes:
    """Recover the master bytes for a 'fresh process' reopen."""
    return ring._master


class TestOrphanCleanup:
    def test_unreferenced_blobs_are_deleted_and_their_keys_shredded(self, store: Store) -> None:
        keep, drop = b"keep me", b"drop me"
        keep_sha, drop_sha = (hashlib.sha256(x).hexdigest() for x in (keep, drop))
        store.upsert_documents(SITE, [file_doc("2:1:a", keep_sha), file_doc("2:2:b", drop_sha)])
        store.put_blob(io.BytesIO(keep), expected_sha256=keep_sha)
        store.put_blob(io.BytesIO(drop), expected_sha256=drop_sha)
        store.tombstone_documents(SITE, ["2:2:b"])
        assert store.delete_orphan_blobs() == [drop_sha]
        assert store.blob_exists(keep_sha)
        assert not store.blob_exists(drop_sha)
        assert not store.blob_path(drop_sha).exists()
        assert store.wrapped_key("blob", drop_sha) is None

    def test_blob_shared_by_another_site_is_kept(self, store: Store) -> None:
        data = b"shared"
        sha = hashlib.sha256(data).hexdigest()
        store.upsert_documents(SITE, [file_doc("2:1:a", sha)])
        store.upsert_documents(OTHER, [file_doc("2:1:a", sha)])
        store.put_blob(io.BytesIO(data), expected_sha256=sha)
        store.tombstone_documents(SITE, ["2:1:a"])
        assert store.delete_orphan_blobs() == []


class TestMaintenance:
    def test_reset_extraction_all_and_failed_only(self, store: Store) -> None:
        store.upsert_documents(SITE, [doc("2:1:a"), doc("2:2:b")])
        store.record_extraction(SITE, "2:1:a", extract_version=1)
        store.record_extraction(SITE, "2:2:b", extract_version=1, skip_reason="fetch: 503")
        assert store.reset_extraction(only_skipped=True) == 1
        assert store.document(SITE, "2:2:b").needs_extraction
        assert not store.document(SITE, "2:1:a").needs_extraction
        assert store.reset_extraction() == 1  # b is already pending; a gets reset

    def test_reembed_after_dimension_change(self, tmp_path: Path, ring: KeyRing) -> None:
        with Store(tmp_path / "x.db", ring, embed_dim=3) as s:
            s.upsert_documents(SITE, [doc()])
            (cid,) = s.replace_content(SITE, "2:10:intro", [], [ChunkIn(ordinal=0, text="t")])
            s.set_embedding(cid, [1.0, 0.0, 0.0])
            s.cache_embedding("h" * 64, "k", [1.0, 0.0, 0.0])
        with Store(tmp_path / "x.db", ring, embed_dim=5, reset_embeddings=True) as s:
            assert s.pending_vector_count() == 1
            assert s.cached_embedding("h" * 64, "k") is None
        with Store(tmp_path / "x.db", ring, embed_dim=5) as s:
            assert s.embed_dim == 5

    def test_rotate_master_key(self, tmp_path: Path, ring: KeyRing) -> None:
        import os

        new = KeyRing(os.urandom(32))
        with Store(tmp_path / "x.db", ring, embed_dim=3) as s:
            secret = s.add_site(SITE, name="A", url="https://moodle.a.test")
            s.upsert_documents(SITE, [doc()])
            assert s.rotate_master_key(new) > 0
        with Store(tmp_path / "x.db", new, embed_dim=3) as s:
            assert s.authenticate_site(secret) is not None
            assert s.document(SITE, "2:10:intro").text == "Willkommen im Kurs Netzwerke."
        with pytest.raises(DatabaseKeyError), Store(tmp_path / "x.db", ring, embed_dim=3):
            pass
