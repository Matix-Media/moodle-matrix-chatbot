"""Blob storage + fetch bookkeeping for `cron`'s Store-free `Fetcher` — see
`bsbot.cron.fetcher.FetchCache` and specs/019-microservice-split.md. Every
handler here is a thin, direct wrapper around one `Store` method.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from bsbot.api.auth import verify_token
from bsbot.api.index.store import Store
from bsbot.api.internal_schemas import (
    BlobResponse,
    FetchFailureRequest,
    FetchRecordResponse,
    RecordFetchRequest,
    TouchFetchRequest,
)

router = APIRouter(dependencies=[Depends(verify_token)])


@router.get("/internal/fetch-record", response_model=FetchRecordResponse)
async def fetch_record(request: Request, url: str) -> FetchRecordResponse:
    store: Store = request.app.state.store
    record = store.fetch_record(url)
    blob_exists = (
        record is not None and record.sha256 is not None and store.blob_exists(record.sha256)
    )
    return FetchRecordResponse(record=record, blob_exists=blob_exists)


@router.post("/internal/fetch-record")
async def record_fetch(request: Request, body: RecordFetchRequest) -> dict[str, str]:
    store: Store = request.app.state.store
    store.record_fetch(
        body.url,
        sha256=body.sha256,
        etag=body.etag,
        last_modified=body.last_modified,
        moodle_timemodified=body.moodle_timemodified,
        fetched_at=body.fetched_at,
        checked_at=body.checked_at,
    )
    return {"status": "ok"}


@router.post("/internal/fetch-record/touch")
async def touch_fetch(request: Request, body: TouchFetchRequest) -> dict[str, str]:
    request.app.state.store.touch_fetch(body.url, checked_at=body.checked_at)
    return {"status": "ok"}


@router.post("/internal/fetch-failures")
async def record_fetch_failure(request: Request, body: FetchFailureRequest) -> dict[str, str]:
    request.app.state.store.record_fetch_failure(
        body.url, error=body.error, checked_at=body.checked_at
    )
    return {"status": "ok"}


@router.get("/internal/blobs/{sha256}")
async def read_blob(request: Request, sha256: str) -> Response:
    store: Store = request.app.state.store
    if not store.blob_exists(sha256):
        raise HTTPException(status_code=404, detail="no such blob")
    return Response(content=store.read_blob(sha256), media_type="application/octet-stream")


@router.head("/internal/blobs/{sha256}")
async def blob_exists(request: Request, sha256: str) -> Response:
    # A separate route, not relying on FastAPI's (absent, in this version)
    # auto-HEAD-for-GET support — lets CronApiClient.blob_exists() check
    # without transferring the blob body just to test for its presence.
    store: Store = request.app.state.store
    return Response(status_code=200 if store.blob_exists(sha256) else 404)


@router.post("/internal/blobs", response_model=BlobResponse)
async def put_blob(request: Request) -> BlobResponse:
    data = await request.body()
    sha256 = request.app.state.store.put_blob(data)
    return BlobResponse(sha256=sha256)
