"""`cron` sends resolved segments here after fetch+extract; this is where PII-
tokenization, chunking, augmentation, embedding-relevant metadata, and storage
all happen — `Indexer.index_segments()`, unchanged logic, called remotely
instead of locally. See specs/019-microservice-split.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from bsbot.api.auth import verify_token
from bsbot.api.index.store import Store, StoreError
from bsbot.api.indexer import Indexer, IndexStats
from bsbot.api.internal_schemas import SegmentsRequest, SegmentsResponse
from bsbot.shared.chunk import Segment

router = APIRouter(dependencies=[Depends(verify_token)])


@router.post("/internal/documents/{doc_id}/segments", response_model=SegmentsResponse)
async def index_segments(request: Request, doc_id: str, body: SegmentsRequest) -> SegmentsResponse:
    store: Store = request.app.state.store
    indexer: Indexer = request.app.state.indexer
    try:
        document = store.document(doc_id)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    segments = (
        [Segment(text=s.text, page=s.page, label=s.label, meta=s.meta) for s in body.segments]
        if body.segments is not None
        else None
    )
    stats = IndexStats()
    await indexer.index_segments(document, segments, body.blob_sha256, stats)
    return SegmentsResponse(
        indexed=stats.indexed, chunks=stats.chunks, skipped=stats.skipped, failed=stats.failed
    )
