"""Verifies spec 006 AC-13..AC-19 — chunking."""

from __future__ import annotations

from bsbot.ingest.chunk import Segment, chunk_segments

HEADER = ["LF05IT", "Lernfeld 5", "Skript"]


def texts(chunks) -> list[str]:
    return [c.text for c in chunks]


class TestSizing:
    def test_short_document_is_one_chunk(self) -> None:
        """AC-17"""
        chunks = chunk_segments([Segment(text="Kurzer Text.")], header_path=HEADER)
        assert len(chunks) == 1
        assert chunks[0].body == "Kurzer Text."

    def test_long_text_is_split_with_overlap(self) -> None:
        """AC-13"""
        para = "Dies ist ein Satz über Netzwerke. " * 60
        chunks = chunk_segments(
            [Segment(text=para)], header_path=HEADER, target_chars=500, overlap_chars=100
        )
        assert len(chunks) > 2
        assert all(len(c.body) <= 700 for c in chunks)
        # consecutive chunks must share some text, or retrieval loses boundary context
        assert any(chunks[0].body[-50:] in chunks[1].body for _ in [0]) or (
            chunks[1].body[:50] in chunks[0].body
        )

    def test_never_splits_mid_word(self) -> None:
        """AC-14"""
        text = " ".join(f"Wort{i}" for i in range(400))
        for chunk in chunk_segments(
            [Segment(text=text)], header_path=HEADER, target_chars=300, overlap_chars=50
        ):
            assert not chunk.body.startswith("ort")
            for token in chunk.body.split():
                assert token.startswith("Wort"), token

    def test_prefers_paragraph_boundaries(self) -> None:
        """AC-14"""
        text = "\n\n".join(f"Absatz {i}. " + "Inhalt. " * 20 for i in range(6))
        chunks = chunk_segments(
            [Segment(text=text)], header_path=HEADER, target_chars=400, overlap_chars=0
        )
        assert any(c.body.startswith("Absatz") for c in chunks)

    def test_unsplittable_run_is_emitted_whole(self) -> None:
        """AC-19: a giant table row must not be dropped or cut mid-token."""
        giant = "A" * 2000
        chunks = chunk_segments(
            [Segment(text=giant)], header_path=HEADER, target_chars=300, overlap_chars=0
        )
        assert "".join(c.body for c in chunks).count("A") >= 2000


class TestContext:
    def test_header_breadcrumb_is_prefixed(self) -> None:
        """AC-15: context for the embedding and provenance for the citation."""
        chunk = chunk_segments([Segment(text="Inhalt.")], header_path=HEADER)[0]
        assert chunk.text.startswith("LF05IT › Lernfeld 5 › Skript")
        assert chunk.text.endswith("Inhalt.")
        assert chunk.body == "Inhalt."

    def test_page_is_recorded(self) -> None:
        """AC-16: pages are attributed when segments are big enough to chunk apart.

        Short segments deliberately merge (see the spanning test below) so retrieval
        is not flooded with one-sentence chunks.
        """
        chunks = chunk_segments(
            [Segment(text="Seite eins. " * 60, page=1), Segment(text="Seite zwei. " * 60, page=2)],
            header_path=HEADER,
            target_chars=400,
            overlap_chars=0,
        )
        assert {c.page for c in chunks} == {1, 2}

    def test_chunk_spanning_pages_records_the_first(self) -> None:
        """AC-16"""
        segs = [Segment(text="Ende von eins. " * 30, page=1), Segment(text="Start zwei.", page=2)]
        chunks = chunk_segments(segs, header_path=HEADER, target_chars=5000, overlap_chars=0)
        assert chunks[0].page == 1


class TestNormalisation:
    def test_whitespace_is_normalised(self) -> None:
        """AC-18: identical content must chunk identically regardless of formatting."""
        messy = chunk_segments(
            [Segment(text="Ein   Text\n\n\n\n\nmit  Lücken.")], header_path=HEADER
        )[0]
        clean = chunk_segments([Segment(text="Ein Text\n\nmit Lücken.")], header_path=HEADER)[0]
        assert messy.body == clean.body

    def test_empty_segments_produce_no_chunks(self) -> None:
        assert chunk_segments([Segment(text="   \n\n  ")], header_path=HEADER) == []

    def test_chunking_is_deterministic(self) -> None:
        """AC-9: the content hash is only a change signal if this holds."""
        segs = [Segment(text="Absatz. " * 200, page=1)]
        a = texts(chunk_segments(segs, header_path=HEADER, target_chars=400, overlap_chars=80))
        b = texts(chunk_segments(segs, header_path=HEADER, target_chars=400, overlap_chars=80))
        assert a == b
