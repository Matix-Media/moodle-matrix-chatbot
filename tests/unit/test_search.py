"""Verifies spec 007 AC-7..AC-15 — hybrid retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from bsbot.index.search import HybridSearcher, fts5_escape, reciprocal_rank_fusion
from bsbot.index.store import Store
from bsbot.ingest.model import ContentItem, ContentKind


def doc(doc_id: str, **kw) -> ContentItem:
    defaults = dict(
        doc_id=doc_id,
        course_id=1,
        course_name="LF05IT",
        section_name="Lernfeld 5",
        module_id=1,
        module_name="Skript",
        modname="resource",
        title="Skript",
        kind=ContentKind.INLINE,
        header_path=["LF05IT", "Lernfeld 5", "Skript"],
        timemodified=100,
        module_url="https://m.example/mod/page/view.php?id=1",
    )
    return ContentItem(**{**defaults, **kw})


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "index.db", embed_dim=3) as s:
        yield s


@pytest.fixture
def populated(store: Store) -> Store:
    corpus = {
        "exam": ("Die Abschlussprüfung Teil 1 findet am 15.03.2026 statt.", [1.0, 0.0, 0.0]),
        "net": ("Netzwerke und Protokolle: TCP/IP Grundlagen im Lernfeld 9.", [0.0, 1.0, 0.0]),
        "cake": ("Das Rezept für Apfelkuchen benötigt 200g Mehl.", [0.0, 0.0, 1.0]),
    }
    store.persist_crawl([doc(d) for d in corpus])
    for doc_id, (text, vector) in corpus.items():
        ids = store.replace_chunks(doc_id, [(text, {"ordinal": 0})], header_text="LF05IT › X")
        store.set_embedding(ids[0], vector)
    return store


class TestFusion:
    def test_rrf_rewards_agreement(self) -> None:
        """AC-10: found by both retrievers beats found by one."""
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
        assert fused[0][0] in ("a", "b")
        assert dict(fused)["a"] > dict(fused)["c"]
        assert dict(fused)["b"] > dict(fused)["d"]

    def test_single_list_hit_still_ranks(self) -> None:
        """AC-9: fusion must not require agreement."""
        fused = dict(reciprocal_rank_fusion([["a"], ["b"]], k=60))
        assert "a" in fused and "b" in fused

    def test_empty_lists_are_safe(self) -> None:
        assert reciprocal_rank_fusion([[], []], k=60) == []


class TestFts5Escaping:
    @pytest.mark.parametrize(
        "raw",
        [
            'Wann ist die "Prüfung"?',
            "LF5 AND NOT OR",
            "Was NEAR bedeutet *",
            'quote " unbalanced',
            "col:on and (parens)",
            "-minus +plus",
            "",
        ],
    )
    def test_user_input_never_breaks_the_query(self, populated: Store, raw: str) -> None:
        """AC-12: a student's question is not FTS5 syntax."""
        searcher = HybridSearcher(populated, embedder=None)
        searcher.search(raw, limit=5)  # must not raise

    def test_escaping_preserves_terms(self) -> None:
        assert "Prüfung" in fts5_escape('Die "Prüfung"')


class TestKeywordSearch:
    def test_exact_identifier_is_found(self, populated: Store) -> None:
        """AC-7: the case embeddings are bad at."""
        hits = HybridSearcher(populated, embedder=None).search("15.03.2026", limit=3)
        assert hits[0].doc_id == "exam"

    def test_umlaut_folding_both_directions(self, populated: Store) -> None:
        """AC-13: students routinely type 'Abschlussprufung' without the umlaut."""
        searcher = HybridSearcher(populated, embedder=None)
        assert searcher.search("Abschlussprufung", limit=3)[0].doc_id == "exam"
        assert searcher.search("Abschlussprüfung", limit=3)[0].doc_id == "exam"

    def test_prefix_matching_handles_german_inflection(self, populated: Store) -> None:
        """AC-13b: 'Netzwerk' must find 'Netzwerke'; German inflects constantly."""
        hits = HybridSearcher(populated, embedder=None).search("Netzwerk", limit=3)
        assert hits and hits[0].doc_id == "net"

    def test_compound_suffix_is_left_to_the_vector_retriever(self, populated: Store) -> None:
        """Documents the deliberate division of labour.

        FTS5 tokenises whole words, so keyword search cannot see 'Prüfung' inside
        'Abschlussprüfung'. That is exactly the gap the embedding retriever closes —
        it is a design boundary, not a bug, and this test pins it down so a future
        change to one retriever does not silently assume the other's job.
        """
        assert HybridSearcher(populated, embedder=None).search("Prüfung", limit=3) == []

        class Embedder:
            def embed_query(self, text: str) -> list[float]:
                return [1.0, 0.0, 0.0]  # points at 'exam'

        hits = HybridSearcher(populated, embedder=Embedder()).search("Prüfung", limit=3)
        assert hits[0].doc_id == "exam"

    def test_works_without_embedder(self, populated: Store) -> None:
        """AC-14: no API key must not mean no answers."""
        hits = HybridSearcher(populated, embedder=None).search("Netzwerke", limit=3)
        assert hits and hits[0].doc_id == "net"


class TestHybridSearch:
    def test_vector_only_match_is_found(self, populated: Store) -> None:
        """AC-9: semantic hit with no lexical overlap at all."""

        class Embedder:
            def embed_query(self, text: str) -> list[float]:
                return [0.0, 1.0, 0.0]  # points at 'net'

        hits = HybridSearcher(populated, embedder=Embedder()).search("Datenübertragung", limit=3)
        assert any(h.doc_id == "net" for h in hits)

    def test_results_carry_citation_provenance(self, populated: Store) -> None:
        """AC-15"""
        hit = HybridSearcher(populated, embedder=None).search("Abschlussprüfung", limit=1)[0]
        assert hit.course_name == "LF05IT"
        assert hit.module_url == "https://m.example/mod/page/view.php?id=1"
        assert hit.header_text
        assert hit.text

    def test_native_document_reports_moodle_timemodified_as_source_date(self, store: Store) -> None:
        """A Moodle-owned file's own timemodified is trustworthy — use it directly."""
        store.persist_crawl([doc("a", timemodified=555)])
        ids = store.replace_chunks("a", [("Inhalt.", {"ordinal": 0})], header_text="X")
        store.set_embedding(ids[0], [1.0, 0.0, 0.0])
        hit = HybridSearcher(store, embedder=None).search("Inhalt", limit=1)[0]
        assert hit.source_date == 555

    def test_external_document_reports_content_changed_at_not_timemodified(
        self, store: Store
    ) -> None:
        """external_url set = reached through an adapter (TaskCards, YouTube...).

        Moodle's timemodified there is when the *link* was pasted in, not when the
        destination content changed — content_changed_at (spec: only advances on a
        genuine text diff, see Store.record_extraction) is the honest signal.
        """
        store.persist_crawl(
            [doc("a", timemodified=111, external_url="https://taskcards.de/#/board/x")]
        )
        store.record_extraction("a", text_sha256="t", extract_version=1, now=777)
        ids = store.replace_chunks("a", [("Inhalt.", {"ordinal": 0})], header_text="X")
        store.set_embedding(ids[0], [1.0, 0.0, 0.0])
        hit = HybridSearcher(store, embedder=None).search("Inhalt", limit=1)[0]
        assert hit.source_date == 777

    def test_tombstoned_documents_never_surface(self, populated: Store) -> None:
        """AC-11: stale answers are worse than missing ones."""
        populated.persist_crawl([doc("net"), doc("cake")])  # 'exam' disappears
        hits = HybridSearcher(populated, embedder=None).search("Abschlussprüfung", limit=5)
        assert all(h.doc_id != "exam" for h in hits)

    def test_limit_is_respected(self, populated: Store) -> None:
        assert len(HybridSearcher(populated, embedder=None).search("Lernfeld", limit=1)) <= 1

    def test_no_match_returns_empty(self, populated: Store) -> None:
        assert HybridSearcher(populated, embedder=None).search("Quantenchromodynamik") == []


class TestNeighbors:
    """A document like a Blockplan is one continuous source arbitrarily cut into
    fixed-size chunks — an adjacent chunk is often topically continuous even when
    it individually ranks far outside the retrieval window for a question's
    wording. See AnswerPipeline._expanded_body for how this gets used.
    """

    def test_returns_chunks_within_radius_in_document_order(self, store: Store) -> None:
        store.persist_crawl([doc("a")])
        ids = store.replace_chunks(
            "a",
            [(f"chunk {i}", {"ordinal": i, "body": f"body {i}"}) for i in range(5)],
            header_text="LF05IT › X",
        )
        neighbors = HybridSearcher(store, embedder=None).neighbors(ids[2], radius=1)
        assert [n.chunk_id for n in neighbors] == ids[1:4]

    def test_radius_is_clamped_at_document_boundaries(self, store: Store) -> None:
        store.persist_crawl([doc("a")])
        ids = store.replace_chunks(
            "a",
            [(f"chunk {i}", {"ordinal": i, "body": f"body {i}"}) for i in range(3)],
            header_text="LF05IT › X",
        )
        neighbors = HybridSearcher(store, embedder=None).neighbors(ids[0], radius=5)
        assert [n.chunk_id for n in neighbors] == ids

    def test_does_not_cross_into_a_different_document(self, store: Store) -> None:
        store.persist_crawl([doc("a"), doc("b")])
        a_ids = store.replace_chunks(
            "a", [("a0", {"ordinal": 0}), ("a1", {"ordinal": 1})], header_text="X"
        )
        b_ids = store.replace_chunks("b", [("b0", {"ordinal": 0})], header_text="X")
        neighbors = HybridSearcher(store, embedder=None).neighbors(a_ids[-1], radius=5)
        assert all(n.doc_id == "a" for n in neighbors)
        assert b_ids[0] not in [n.chunk_id for n in neighbors]

    def test_unknown_chunk_id_returns_nothing(self, store: Store) -> None:
        assert HybridSearcher(store, embedder=None).neighbors(999999, radius=2) == []

    def test_body_is_populated_separately_from_the_prefixed_text(self, store: Store) -> None:
        store.persist_crawl([doc("a")])
        ids = store.replace_chunks(
            "a",
            [("Kurs › X\n\nNur der Inhalt.", {"ordinal": 0, "body": "Nur der Inhalt."})],
            header_text="X",
        )
        neighbors = HybridSearcher(store, embedder=None).neighbors(ids[0], radius=0)
        assert neighbors[0].body == "Nur der Inhalt."


class TestChannelScopedRetrieval:
    def test_channel_scoping_retrieval(self, store: Store) -> None:
        # Moodle doc
        store.persist_crawl([doc("moodle_doc", title="Allgemeines Skript")])
        store.replace_chunks(
            "moodle_doc",
            [("LF05IT › Skript\n\nPrüfungsthemen für alle Klassen.", {"ordinal": 0})],
            header_text="LF05IT › Skript",
        )

        # Moderator message in Room A
        store.index_matrix_message(
            room_id="!roomA:example.org",
            event_id="$msgA",
            sender="@lehrerA:example.org",
            text="Klasse A hat die Prüfung am Montag.",
            timemodified=1000,
            room_name="Klasse A",
        )

        # Moderator message in Room B
        store.index_matrix_message(
            room_id="!roomB:example.org",
            event_id="$msgB",
            sender="@lehrerB:example.org",
            text="Klasse B hat die Prüfung am Dienstag.",
            timemodified=1000,
            room_name="Klasse B",
        )

        searcher = HybridSearcher(store, embedder=None)

        # Query from Room A: should get Moodle doc + Room A msg, but NOT Room B msg
        hits_a = searcher.search("Prüfung", room_id="!roomA:example.org")
        doc_ids_a = {h.doc_id for h in hits_a}
        assert "moodle_doc" in doc_ids_a
        assert "matrix:!roomA:example.org:$msgA" in doc_ids_a
        assert "matrix:!roomB:example.org:$msgB" not in doc_ids_a

        # Query from Room B: should get Moodle doc + Room B msg, but NOT Room A msg
        hits_b = searcher.search("Prüfung", room_id="!roomB:example.org")
        doc_ids_b = {h.doc_id for h in hits_b}
        assert "moodle_doc" in doc_ids_b
        assert "matrix:!roomB:example.org:$msgB" in doc_ids_b
        assert "matrix:!roomA:example.org:$msgA" not in doc_ids_b

        # Query with no room_id: should get Moodle doc, but no matrix messages
        hits_none = searcher.search("Prüfung", room_id=None)
        doc_ids_none = {h.doc_id for h in hits_none}
        assert "moodle_doc" in doc_ids_none
        assert not any(d.startswith("matrix:") for d in doc_ids_none)
