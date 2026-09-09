"""`cron` persists a crawl here and gets back what still needs fetching —
replaces cron's old direct `store.persist_crawl(...)` call. See
specs/015-microservice-split.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from bsbot.index.store import Document, Store
from bsbot.ingest.extract import EXTRACT_VERSION
from bsbot.web.auth import verify_token
from bsbot.web.internal_schemas import CrawlResultRequest, CrawlResultResponse

router = APIRouter(dependencies=[Depends(verify_token)])


@router.post("/internal/crawl-result", response_model=CrawlResultResponse)
async def crawl_result(request: Request, body: CrawlResultRequest) -> CrawlResultResponse:
    store: Store = request.app.state.store
    store.persist_crawl(body.items)
    pending = store.documents_needing_extraction(extract_version=EXTRACT_VERSION)
    return CrawlResultResponse(persisted=len(body.items), pending=pending)


@router.get("/internal/documents/pending", response_model=list[Document])
async def pending_documents(request: Request) -> list[Document]:
    # A separate call from crawl_result's own `pending` (not just its return
    # value) so `cron`'s sync and index steps stay independently retriable —
    # index must still know what's pending even on a cycle where sync itself
    # failed and never got to persist anything new.
    store: Store = request.app.state.store
    return store.documents_needing_extraction(extract_version=EXTRACT_VERSION)
