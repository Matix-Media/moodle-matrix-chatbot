"""Scoped retrieval: hybrid (FTS + vectors), text-only, always within an access scope."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from factories import doc

from moodle_llm_chat.access.scope import AccessScope, Capability
from moodle_llm_chat.index.models import ChunkIn, SegmentIn
from moodle_llm_chat.index.search import Searcher, fts5_query, reciprocal_rank_fusion
from moodle_llm_chat.index.store import Store

S = "site-a"


def scope(*cmids: int, site: str = S) -> AccessScope:
    return AccessScope(
        site_id=site,
        user_id=1,
        course_ids=frozenset({2, 3}),
        cmids=frozenset(cmids),
        caps=frozenset({Capability.READ}),
    )


class FakeEmbedder:
    """Maps a handful of known words onto fixed directions."""

    AXES: ClassVar[dict[str, list[float]]] = {
        "vlan": [1.0, 0.0, 0.0],
        "routing": [0.0, 1.0, 0.0],
        "prüfung": [0.0, 0.0, 1.0],
    }

    async def embed_query(self, text: str) -> list[float]:
        for word, vector in self.AXES.items():
            if word in text.lower():
                return vector
        return [0.5, 0.5, 0.5]


def index(
    store: Store, doc_id: str, cmid: int, text: str, *, vector: list[float] | None = None, **kw: Any
) -> int:
    store.upsert_documents(S, [doc(doc_id, cmid=cmid, **kw)])
    (cid,) = store.replace_content(
        S,
        doc_id,
        [SegmentIn(ordinal=0, text=text)],
        [ChunkIn(ordinal=0, text=text, header_text="LF7")],
    )
    if vector is not None:
        store.set_embedding(cid, vector)
    return cid


@pytest.fixture
def populated(store: Store) -> Store:
    index(
        store,
        "2:10:a",
        10,
        "Ein VLAN trennt Broadcast-Domänen im Netzwerk.",
        vector=[1, 0, 0],
        timemodified=100,
    )
    index(
        store,
        "2:11:b",
        11,
        "Statisches Routing mit Routingtabellen.",
        vector=[0, 1, 0],
        timemodified=200,
    )
    index(
        store,
        "3:20:c",
        20,
        "Die Abschlussprüfung findet im Mai statt.",
        vector=[0, 0, 1],
        timemodified=300,
    )
    index(
        store, "3:21:hidden", 21, "Geheime VLAN Musterlösung.", vector=[1, 0, 0], timemodified=400
    )
    return store


class TestFusion:
    def test_agreement_ranks_first(self) -> None:
        fused = reciprocal_rank_fusion([[1, 2, 3], [2, 1, 4]])
        assert [k for k, _ in fused][:2] in ([1, 2], [2, 1])
        assert dict(fused)[2] > dict(fused)[3]

    def test_empty(self) -> None:
        assert reciprocal_rank_fusion([[], []]) == []


class TestQuerySyntax:
    @pytest.mark.parametrize(
        "raw", ["Was NEAR bedeutet *", 'a"b', "-", "AND OR NOT", "col:val", "(((", ""]
    )
    def test_user_input_never_breaks_fts(self, populated: Store, raw: str) -> None:
        match = fts5_query(raw)
        if match:
            populated.fts_chunk_ids(S, match, limit=5)

    def test_phrase_mode(self) -> None:
        assert fts5_query("statisches routing", mode="phrase") == '"statisches routing"'

    def test_all_mode_requires_every_term(self) -> None:
        assert " AND " in fts5_query("statisches routing", mode="all")


class TestHybrid:
    async def test_out_of_scope_documents_never_surface(self, populated: Store) -> None:
        hits = await Searcher(populated, FakeEmbedder()).hybrid(scope(10, 11, 20), "VLAN", k=10)
        assert "3:21:hidden" not in {h.document.doc_id for h in hits}
        assert hits[0].document.doc_id == "2:10:a"

    async def test_other_sites_never_surface(self, populated: Store) -> None:
        hits = await Searcher(populated, FakeEmbedder()).hybrid(
            scope(10, site="other"), "VLAN", k=5
        )
        assert hits == []

    async def test_vector_only_match(self, populated: Store) -> None:
        """'Prüfung' alone does not hit 'Abschlussprüfung' lexically; the vector does."""
        hits = await Searcher(populated, FakeEmbedder()).hybrid(scope(10, 11, 20), "Prüfung", k=3)
        assert hits[0].document.doc_id == "3:20:c"
        assert "vector" in hits[0].sources

    async def test_works_without_embedder(self, populated: Store) -> None:
        hits = await Searcher(populated, None).hybrid(scope(10, 11, 20), "Routing", k=3)
        assert [h.document.doc_id for h in hits] == ["2:11:b"]

    async def test_time_filter(self, populated: Store) -> None:
        hits = await Searcher(populated, FakeEmbedder()).hybrid(
            scope(10, 11, 20), "Netzwerk Routing Prüfung", k=10, since=150
        )
        assert {h.document.doc_id for h in hits} == {"2:11:b", "3:20:c"}

    async def test_doc_id_filter(self, populated: Store) -> None:
        hits = await Searcher(populated, FakeEmbedder()).hybrid(
            scope(10, 11, 20), "VLAN Routing", k=10, doc_ids=["2:11:b"]
        )
        assert [h.document.doc_id for h in hits] == ["2:11:b"]

    async def test_limit(self, populated: Store) -> None:
        hits = await Searcher(populated, FakeEmbedder()).hybrid(scope(10, 11, 20), "das", k=1)
        assert len(hits) <= 1

    async def test_hits_carry_decrypted_text_and_metadata(self, populated: Store) -> None:
        (hit, *_) = await Searcher(populated, FakeEmbedder()).hybrid(scope(10), "VLAN", k=1)
        assert "Broadcast" in hit.chunk.text
        assert hit.document.module_url


class TestText:
    async def test_umlaut_folding(self, populated: Store) -> None:
        hits = await Searcher(populated, None).text(scope(10, 11, 20), "Domanen", k=5)
        assert [h.document.doc_id for h in hits] == ["2:10:a"]

    async def test_prefix_absorbs_inflection(self, populated: Store) -> None:
        hits = await Searcher(populated, None).text(scope(10, 11, 20), "Routingtabelle", k=5)
        assert [h.document.doc_id for h in hits] == ["2:11:b"]

    async def test_phrase(self, populated: Store) -> None:
        searcher = Searcher(populated, None)
        assert await searcher.text(scope(10, 11), "Routing mit", k=5, mode="phrase")
        assert not await searcher.text(scope(10, 11), "mit Routing", k=5, mode="phrase")
