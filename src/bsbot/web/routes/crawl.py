"""`cron` persists a crawl here and gets back what still needs fetching —
replaces cron's old direct `store.persist_crawl(...)` call. See
specs/015-microservice-split.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from bsbot.index.store import Store
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
