"""Embed every chunk that doesn't yet have a vector — the body of the old
`sync_loop.embed_once`, now running server-side against `api`'s own already-
built `Store`/`GeminiEmbedder` instead of `cron` building its own. `cron`
just triggers this after an index pass. See specs/019-microservice-split.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from bsbot.index.store import Store
from bsbot.llm.embed import GeminiEmbedder
from bsbot.web.auth import verify_token
from bsbot.web.internal_schemas import EmbedPendingResponse

router = APIRouter(dependencies=[Depends(verify_token)])


@router.post("/internal/embed-pending", response_model=EmbedPendingResponse)
async def embed_pending(request: Request) -> EmbedPendingResponse:
    store: Store = request.app.state.store
    embedder: GeminiEmbedder = request.app.state.embedder
    pii_tokenizer = request.app.state.pii_tokenizer
    rows = store.connection.execute(
        "SELECT c.chunk_id, c.text, c.text_tokenized FROM chunks c "
        "LEFT JOIN chunks_vec v ON v.chunk_id = c.chunk_id "
        "WHERE v.chunk_id IS NULL ORDER BY c.chunk_id"
    ).fetchall()
    if not rows:
        return EmbedPendingResponse(embedded=0, total=0)

    # Never `r["text_tokenized"] or r["text"]`: NULL means the row predates the
    # egress-boundary migration and has no Gemini-facing form, so the fallback
    # would send real names (spec 013 AC-35). Skip until a reindex produces one.
    pending = [r for r in rows if pii_tokenizer is None or r["text_tokenized"] is not None]
    if not pending:
        return EmbedPendingResponse(embedded=0, total=len(rows))

    texts = [(r["text_tokenized"] if pii_tokenizer is not None else r["text"]) for r in pending]
    vectors = embedder.embed_documents(texts, skip_failures=True)
    done = 0
    for row, vector in zip(pending, vectors, strict=True):
        if vector is not None:
            store.set_embedding(row["chunk_id"], vector)
            done += 1
    return EmbedPendingResponse(embedded=done, total=len(rows))
