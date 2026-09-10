"""Hybrid retrieval — see ``specs/007-retrieval.md``.

BM25 and vector search fail in complementary ways on this corpus, which is the
condition under which fusing them beats either alone:

* ``LF05``, ``15.03.2026``, a teacher's surname — lexical needles. BM25 finds them
  exactly; embeddings blur them into a neighbourhood of "school date-ish things".
* "wann is die prüfung" against a document titled *Termin der Abschlussprüfung Teil 1*
  — no useful lexical overlap. Embeddings bridge it; BM25 cannot.
* "der Stundenplan für nächste Woche Montag" against a Blockplan chunked one week per
  chunk — every week is an equally good BM25/embedding match for "Stundenplan", so
  neither ranks the one week the question actually means above the other five. Only
  the *resolved* calendar date (computed from the question, not present in it as
  text) distinguishes them — see ``target_date``/``_date_search`` below, and
  ``bsbot.rag.pipeline._detect_target_date`` for where that date comes from.

Reciprocal Rank Fusion combines all ranked lists without needing their scores to be
comparable, which matters because BM25 scores, cosine distances, and a plain
date-substring match are not on any shared scale.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Protocol

import structlog
from pydantic import BaseModel

from bsbot.index.store import Store, normalise
from bsbot.pii.tokenizer import PiiTokenizer

log = structlog.get_logger(__name__)

#: RRF damping. 60 is the value from the original paper and is not sensitive.
RRF_K = 60
#: How deep each retriever goes before fusion. Generous: fusion needs candidates.
CANDIDATE_DEPTH = 50


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class SearchHit(BaseModel):
    """A retrieved chunk with everything needed to cite it."""

    chunk_id: int
    doc_id: str
    text: str
    #: The chunk's own content, without the "Course › Section › Module" prefix
    #: baked into ``text``. Needed to splice several chunks together (see
    #: ``AnswerPipeline._expanded_body``) without repeating that prefix once per
    #: chunk merged.
    body: str = ""
    header_text: str
    course_name: str
    module_name: str
    module_url: str | None
    title: str
    page: int | None
    score: float
    sources: list[str] = []
    #: Epoch seconds of the best-available "last changed" signal for this chunk's
    #: document, or None if none is known yet. See ``_hydrate`` for which column
    #: this comes from and why it differs between native and external content.
    source_date: int | None = None


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], *, k: int = RRF_K
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists. ``score = Σ 1/(k + rank)`` (spec 007 AC-8)."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


_TOKEN_RE = re.compile(r"[^\w\-.]+", re.UNICODE)


def fts5_escape(query: str) -> str:
    """Turn a student's question into a safe FTS5 query (AC-12).

    Every token is double-quoted, which neutralises FTS5's operators (``AND``,
    ``NEAR``, ``*``, ``:``, ``-``) so a question like ``Was NEAR bedeutet *`` is
    treated as words rather than as syntax. Tokens are OR-ed because requiring every
    word of a natural-language question to appear would return almost nothing.
    """
    tokens = [t for t in _TOKEN_RE.split(query) if t]
    if not tokens:
        return ""
    parts: list[str] = []
    for token in tokens:
        quoted = '"' + token.replace('"', '""') + '"'
        # Prefix matching absorbs German inflection: "Netzwerk" -> "Netzwerke",
        # "Netzwerken". Short tokens are left exact, since "am*" would match half
        # the corpus. Compound *suffixes* ("Prüfung" in "Abschlussprüfung") are
        # deliberately the vector retriever's job.
        parts.append(f"{quoted}*" if len(token) >= 4 else quoted)
    return " OR ".join(parts)


class HybridSearcher:
    def __init__(
        self,
        store: Store,
        *,
        embedder: Embedder | None,
        candidate_depth: int = CANDIDATE_DEPTH,
        pii_tokenizer: PiiTokenizer | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._depth = candidate_depth
        self._pii_tokenizer = pii_tokenizer

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        room_id: str | None = None,
        #: Self-querying-retriever style metadata filter (see module docstring):
        #: a calendar date resolved from the *question* ("nächste Woche Montag" ->
        #: 2026-09-15), searched as a genuine SQL predicate against each chunk's
        #: own text alongside keyword/vector search — not a Python-side reorder of
        #: whatever those two already happened to retrieve. A Blockplan chunk's
        #: date lives in its content, not in ``source_date`` (that column is the
        #: document's last-modified time, a different thing entirely), so this is
        #: the only way retrieval itself — not just reranking — can be told which
        #: of several near-identical weekly chunks the question actually meant.
        target_date: date | None = None,
    ) -> list[SearchHit]:
        keyword_ids = self._keyword_search(query, room_id=room_id)
        vector_ids = self._vector_search(query, room_id=room_id)
        date_ids = self._date_search(target_date, room_id=room_id) if target_date else []

        ranked = reciprocal_rank_fusion([keyword_ids, vector_ids, date_ids])
        if not ranked:
            return []

        found_by: dict[str, list[str]] = {}
        for chunk_id in keyword_ids:
            found_by.setdefault(chunk_id, []).append("keyword")
        for chunk_id in vector_ids:
            found_by.setdefault(chunk_id, []).append("vector")
        for chunk_id in date_ids:
            found_by.setdefault(chunk_id, []).append("date")

        top = ranked[: limit * 3]
        hits = self._hydrate([key for key, _ in top])
        scores = dict(ranked)
        for hit in hits:
            hit.score = scores.get(str(hit.chunk_id), 0.0)
            hit.sources = found_by.get(str(hit.chunk_id), [])
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _keyword_search(self, query: str, *, room_id: str | None = None) -> list[str]:
        match = fts5_escape(query)
        if not match:
            return []
        try:
            if room_id:
                rows = self._store.connection.execute(
                    """
                    SELECT c.chunk_id
                    FROM chunks_fts f
                    JOIN chunks c ON c.chunk_id = f.rowid
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE chunks_fts MATCH ? AND d.tombstoned_at IS NULL
                      AND (d.doc_id NOT LIKE 'matrix:%' OR d.doc_id LIKE 'matrix:' || ? || ':%')
                    ORDER BY bm25(chunks_fts, 1.0, 0.5)
                    LIMIT ?
                    """,
                    (match, room_id, self._depth),
                ).fetchall()
            else:
                rows = self._store.connection.execute(
                    """
                    SELECT c.chunk_id
                    FROM chunks_fts f
                    JOIN chunks c ON c.chunk_id = f.rowid
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE chunks_fts MATCH ? AND d.tombstoned_at IS NULL
                      AND d.doc_id NOT LIKE 'matrix:%'
                    ORDER BY bm25(chunks_fts, 1.0, 0.5)
                    LIMIT ?
                    """,
                    (match, self._depth),
                ).fetchall()
        except Exception as exc:  # a malformed query must never break the bot
            log.warning("search.fts_failed", error=str(exc))
            return []
        return [str(r[0]) for r in rows]

    def _vector_search(self, query: str, *, room_id: str | None = None) -> list[str]:
        if self._embedder is None:
            return []  # AC-14: keyword-only is still useful
        try:
            vector = normalise(self._embedder.embed_query(query))
        except Exception as exc:
            log.warning("search.embed_failed", error=str(exc))
            return []
        if not vector:
            return []

        import struct

        packed = struct.pack(f"{len(vector)}f", *vector)
        try:
            if room_id:
                rows = self._store.connection.execute(
                    """
                    SELECT v.chunk_id
                    FROM chunks_vec v
                    JOIN chunks c ON c.chunk_id = v.chunk_id
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE v.embedding MATCH ? AND k = ? AND d.tombstoned_at IS NULL
                      AND (d.doc_id NOT LIKE 'matrix:%' OR d.doc_id LIKE 'matrix:' || ? || ':%')
                    ORDER BY distance
                    """,
                    (packed, self._depth, room_id),
                ).fetchall()
            else:
                rows = self._store.connection.execute(
                    """
                    SELECT v.chunk_id
                    FROM chunks_vec v
                    JOIN chunks c ON c.chunk_id = v.chunk_id
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE v.embedding MATCH ? AND k = ? AND d.tombstoned_at IS NULL
                      AND d.doc_id NOT LIKE 'matrix:%'
                    ORDER BY distance
                    """,
                    (packed, self._depth),
                ).fetchall()
        except Exception as exc:
            log.warning("search.vec_failed", error=str(exc))
            return []
        return [str(r[0]) for r in rows]

    def _date_search(self, target_date: date, *, room_id: str | None = None) -> list[str]:
        """Chunks whose own text literally contains ``target_date`` (either
        written form), independent of how keyword/vector search happened to rank
        them.

        Neither BM25 nor embeddings can be trusted to place this chunk inside the
        candidate window: FTS5 tokenises "2026-09-15" fine but competes with every
        other week's chunk on equal topical footing, and embeddings blur dates
        into a neighbourhood of "school date-ish things" (see module docstring).
        A direct substring predicate against the source text sidesteps both —
        this is the metadata-filter half of retrieval, run as SQL rather than as
        a Python-side reorder of an already-fixed candidate pool.
        """
        needles = (f"%{target_date:%d.%m.%Y}%", f"%{target_date:%Y-%m-%d}%")
        try:
            if room_id:
                rows = self._store.connection.execute(
                    """
                    SELECT c.chunk_id
                    FROM chunks c
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE (c.text LIKE ? OR c.text LIKE ?) AND d.tombstoned_at IS NULL
                      AND (d.doc_id NOT LIKE 'matrix:%' OR d.doc_id LIKE 'matrix:' || ? || ':%')
                    LIMIT ?
                    """,
                    (*needles, room_id, self._depth),
                ).fetchall()
            else:
                rows = self._store.connection.execute(
                    """
                    SELECT c.chunk_id
                    FROM chunks c
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE (c.text LIKE ? OR c.text LIKE ?) AND d.tombstoned_at IS NULL
                      AND d.doc_id NOT LIKE 'matrix:%'
                    LIMIT ?
                    """,
                    (*needles, self._depth),
                ).fetchall()
        except Exception as exc:  # a malformed pattern must never break the bot
            log.warning("search.date_search_failed", error=str(exc))
            return []
        return [str(r[0]) for r in rows]

    def _hydrate(self, chunk_ids: list[str]) -> list[SearchHit]:
        if not chunk_ids:
            return []
        marks = ",".join("?" * len(chunk_ids))
        rows = self._store.connection.execute(
            f"""
            SELECT c.chunk_id, c.doc_id, c.text, c.meta, c.header_text, c.page,
                   d.course_name, d.module_name, d.module_url, d.title,
                   -- Moodle's timemodified is only trustworthy for content Moodle
                   -- itself owns. For anything reached through an external adapter
                   -- (external_url set), it reflects when the *link* was pasted in,
                   -- not when the destination content last changed — content_changed_at
                   -- (bumped only on a genuine text diff, see Store.record_extraction)
                   -- is the honest signal there instead.
                   CASE WHEN d.external_url IS NOT NULL
                        THEN d.content_changed_at
                        ELSE d.timemodified
                   END AS source_date
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE c.chunk_id IN ({marks}) AND d.tombstoned_at IS NULL
            """,
            chunk_ids,
        ).fetchall()
        hits = [
            SearchHit(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                text=r["text"],
                body=json.loads(r["meta"]).get("body", "") if r["meta"] else "",
                header_text=r["header_text"],
                page=r["page"],
                course_name=r["course_name"],
                module_name=r["module_name"],
                module_url=r["module_url"],
                title=r["title"],
                score=0.0,
                source_date=r["source_date"],
            )
            for r in rows
        ]
        # PII tokenization (spec 013): `course_name`/`module_name`/`title` are read
        # straight from the raw `documents` table above (chunk text is already
        # tokenized at write time, but these breadcrumb fields are not derived
        # from it) — without this, they would reach the LLM prompt and citations
        # untokenized on every query, independent of anything done at ingest time.
        if self._pii_tokenizer is not None:
            for hit in hits:
                hit.course_name = self._pii_tokenizer.tokenize(hit.course_name)
                hit.module_name = self._pii_tokenizer.tokenize(hit.module_name)
                hit.title = self._pii_tokenizer.tokenize(hit.title)
        return hits

    def neighbors(self, chunk_id: int, *, radius: int) -> list[SearchHit]:
        """Chunks within ``radius`` positions of ``chunk_id`` in the same document,
        in document order, ``chunk_id`` itself included.

        A document like a Blockplan is one continuous source arbitrarily cut into
        fixed-size chunks — an adjacent chunk is often topically continuous (next
        week's schedule right after this week's) even when it individually ranks
        far outside the retrieval window for a given question's wording. Found
        live: the chunk with the actually-asked-about date was never retrieved at
        all, but the chunk right next to it was.
        """
        row = self._store.connection.execute(
            "SELECT doc_id, ordinal FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        if row is None:
            return []
        rows = self._store.connection.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id=? AND ordinal BETWEEN ? AND ? "
            "ORDER BY ordinal",
            (row["doc_id"], row["ordinal"] - radius, row["ordinal"] + radius),
        ).fetchall()
        return self._hydrate([str(r["chunk_id"]) for r in rows])
