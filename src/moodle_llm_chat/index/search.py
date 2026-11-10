"""Scoped retrieval over the encrypted store.

Every query carries an :class:`~moodle_llm_chat.access.scope.AccessScope`; the store
restricts candidates to that site and the user's visible course modules *inside* the
query (FTS join / exact cosine over only in-scope chunks), so restricted content can
never rank, leak through a snippet, or crowd out a visible result.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal, Protocol

from moodle_llm_chat.access.scope import AccessScope
from moodle_llm_chat.index.models import Chunk, Document
from moodle_llm_chat.index.store import Store

RRF_K = 60
CANDIDATE_DEPTH = 50
TextMode = Literal["any", "all", "phrase"]

_TOKEN_RE = re.compile(r"[^\w\-.]+", re.UNICODE)


class QueryEmbedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


@dataclass
class Hit:
    chunk: Chunk
    document: Document
    score: float
    sources: list[str] = field(default_factory=list)


def reciprocal_rank_fusion(
    ranked_lists: list[list[int]], *, k: int = RRF_K
) -> list[tuple[int, float]]:
    """Fuse ranked id lists: ``score = Σ 1 / (k + rank)``."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def fts5_query(query: str, *, mode: TextMode = "any") -> str:
    """Turn free text into a safe FTS5 expression.

    Every token is quoted, neutralising FTS5 operators. ``any`` ORs tokens (natural
    questions rarely contain every word) with prefix matching for tokens of 4+ chars,
    which absorbs German inflection; ``all`` ANDs them; ``phrase`` matches the exact
    word sequence.
    """
    tokens = [t for t in _TOKEN_RE.split(query) if t]
    if not tokens:
        return ""
    if mode == "phrase":
        return _quote(" ".join(tokens))
    parts = [f"{_quote(t)}*" if len(t) >= 4 else _quote(t) for t in tokens]
    return (" AND " if mode == "all" else " OR ").join(parts)


class Searcher:
    def __init__(self, store: Store, embedder: QueryEmbedder | None) -> None:
        self.store = store
        self.embedder = embedder

    async def hybrid(
        self,
        scope: AccessScope,
        query: str,
        *,
        k: int,
        since: int | None = None,
        until: int | None = None,
        doc_ids: Collection[str] | None = None,
    ) -> list[Hit]:
        lexical: list[int] = []
        match = fts5_query(query)
        if match:
            lexical = self.store.fts_chunk_ids(
                scope.site_id,
                match,
                limit=CANDIDATE_DEPTH,
                cmids=scope.cmids,
                doc_ids=doc_ids,
                since=since,
                until=until,
            )
        semantic: list[int] = []
        if self.embedder is not None:
            vector = await self.embedder.embed_query(query)
            semantic = self.store.vector_chunk_ids(
                scope.site_id,
                vector,
                limit=CANDIDATE_DEPTH,
                cmids=scope.cmids,
                doc_ids=doc_ids,
                since=since,
                until=until,
            )
        fused = reciprocal_rank_fusion([lexical, semantic])[:k]
        sources: dict[int, list[str]] = {}
        for name, ids in (("text", lexical), ("vector", semantic)):
            for cid in ids:
                sources.setdefault(cid, []).append(name)
        return self._hydrate(scope, fused, sources)

    async def text(
        self,
        scope: AccessScope,
        query: str,
        *,
        k: int,
        mode: TextMode = "any",
        since: int | None = None,
        until: int | None = None,
        doc_ids: Collection[str] | None = None,
    ) -> list[Hit]:
        match = fts5_query(query, mode=mode)
        if not match:
            return []
        ids = self.store.fts_chunk_ids(
            scope.site_id,
            match,
            limit=k,
            cmids=scope.cmids,
            doc_ids=doc_ids,
            since=since,
            until=until,
        )
        ranked = [(cid, 1.0 / (RRF_K + rank)) for rank, cid in enumerate(ids, start=1)]
        return self._hydrate(scope, ranked, {cid: ["text"] for cid in ids})

    def _hydrate(
        self, scope: AccessScope, ranked: list[tuple[int, float]], sources: dict[int, list[str]]
    ) -> list[Hit]:
        chunks = {c.chunk_id: c for c in self.store.chunks(scope.site_id, [c for c, _ in ranked])}
        docs: dict[str, Document] = {}
        hits: list[Hit] = []
        for cid, score in ranked:
            chunk = chunks.get(cid)
            if chunk is None:
                continue
            if chunk.doc_id not in docs:
                docs[chunk.doc_id] = self.store.document(scope.site_id, chunk.doc_id)
            document = docs[chunk.doc_id]
            if document.cmid not in scope.cmids:  # belt and braces
                continue
            hits.append(Hit(chunk=chunk, document=document, score=score, sources=sources[cid]))
        return hits
