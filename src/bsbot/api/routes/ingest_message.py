"""The one non-Q&A write `matrix` used to make directly (`Store.
index_matrix_message`, moderator-message embedding) — now a call over the
wire instead. See specs/019-microservice-split.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from bsbot.api.auth import verify_token
from bsbot.api.index.store import Store
from bsbot.api.internal_schemas import IngestMessageRequest, IngestMessageResponse

router = APIRouter(dependencies=[Depends(verify_token)])


@router.post("/internal/ingest-message", response_model=IngestMessageResponse)
async def ingest_message(request: Request, body: IngestMessageRequest) -> IngestMessageResponse:
    store: Store = request.app.state.store
    chunk_ids = store.index_matrix_message(
        body.room_id,
        body.event_id,
        body.sender,
        body.text,
        body.timemodified,
        room_name=body.room_name,
        max_history_per_room=body.max_history_per_room,
        embedder=request.app.state.embedder,
        pii_tokenizer=request.app.state.pii_tokenizer,
    )
    return IngestMessageResponse(chunk_ids=chunk_ids)
