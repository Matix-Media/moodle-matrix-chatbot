"""Hybrid retrieval — see ``specs/007-retrieval.md``.

BM25 and vector search fail in complementary ways on this corpus, which is the
condition under which fusing them beats either alone:

* ``LF05``, ``15.03.2026``, a teacher's surname — lexical needles. BM25 finds them
  exactly; embeddings blur them into a neighbourhood of "school date-ish things".
* "wann is die prüfung" against a document titled *Termin der Abschlussprüfung Teil 1*
  — no useful lexical overlap. Embeddings bridge it; BM25 cannot.

Reciprocal Rank Fusion combines the two ranked lists without needing their scores to
be comparable, which matters because BM25 scores and cosine distances are not on any
shared scale.
"""

from __future__ import annotations

import re
from typing import Protocol

import structlog
from pydantic import BaseModel

from bsbot.index.store import Store, normalise

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
    header_text: str
    course_name: str
    module_name: str
    module_url: str | None
    title: str
    page: int | None
    score: float
    sources: list[str] = []


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
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._depth = candidate_depth

    def search(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        keyword_ids = self._keyword_search(query)
        vector_ids = self._vector_search(query)

        ranked = reciprocal_rank_fusion([keyword_ids, vector_ids])
        if not ranked:
            return []

        found_by: dict[str, list[str]] = {}
        for chunk_id in keyword_ids:
            found_by.setdefault(chunk_id, []).append("keyword")
        for chunk_id in vector_ids:
            found_by.setdefault(chunk_id, []).append("vector")

        top = ranked[: limit * 3]
        hits = self._hydrate([key for key, _ in top])
        scores = dict(ranked)
        for hit in hits:
            hit.score = scores.get(str(hit.chunk_id), 0.0)
            hit.sources = found_by.get(str(hit.chunk_id), [])
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _keyword_search(self, query: str) -> list[str]:
        match = fts5_escape(query)
        if not match:
            return []
        try:
            rows = self._store.connection.execute(
                """
                SELECT c.chunk_id
                FROM chunks_fts f
                JOIN chunks c ON c.chunk_id = f.rowid
                JOIN documents d ON d.doc_id = c.doc_id
                WHERE chunks_fts MATCH ? AND d.tombstoned_at IS NULL
                ORDER BY bm25(chunks_fts, 1.0, 0.5)
                LIMIT ?
                """,
                (match, self._depth),
            ).fetchall()
        except Exception as exc:  # a malformed query must never break the bot
            log.warning("search.fts_failed", error=str(exc))
            return []
        return [str(r[0]) for r in rows]

    def _vector_search(self, query: str) -> list[str]:
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
            rows = self._store.connection.execute(
                """
                SELECT v.chunk_id
                FROM chunks_vec v
                JOIN chunks c ON c.chunk_id = v.chunk_id
                JOIN documents d ON d.doc_id = c.doc_id
                WHERE v.embedding MATCH ? AND k = ? AND d.tombstoned_at IS NULL
                ORDER BY distance
                """,
                (packed, self._depth),
            ).fetchall()
        except Exception as exc:
            log.warning("search.vec_failed", error=str(exc))
            return []
        return [str(r[0]) for r in rows]

    def _hydrate(self, chunk_ids: list[str]) -> list[SearchHit]:
        if not chunk_ids:
            return []
        marks = ",".join("?" * len(chunk_ids))
        rows = self._store.connection.execute(
            f"""
            SELECT c.chunk_id, c.doc_id, c.text, c.header_text, c.page,
                   d.course_name, d.module_name, d.module_url, d.title
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE c.chunk_id IN ({marks}) AND d.tombstoned_at IS NULL
            """,
            chunk_ids,
        ).fetchall()
        return [
            SearchHit(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                text=r["text"],
                header_text=r["header_text"],
                page=r["page"],
                course_name=r["course_name"],
                module_name=r["module_name"],
                module_url=r["module_url"],
                title=r["title"],
                score=0.0,
            )
            for r in rows
        ]
