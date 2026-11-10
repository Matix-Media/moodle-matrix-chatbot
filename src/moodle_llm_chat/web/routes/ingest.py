"""Ingest routes: the Moodle plugin pushes courses, documents and blobs.

Every route is idempotent so the plugin's outbox can retry after a lost response:
documents upsert by content hash, blobs by SHA-256, deletes of unknown ids succeed,
and finishing an already-finished sync run is a no-op.

All routes are ``async def``: the store's connection belongs to the event loop thread.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from moodle_llm_chat.index.models import CourseIn, DocumentIn, SyncResult
from moodle_llm_chat.index.store import StoreError, SyncStateError
from moodle_llm_chat.web.auth import SiteDep, StoreDep

router = APIRouter(prefix="/ingest", tags=["ingest"])

SHA256_PATH = Path(pattern=r"^[0-9a-f]{64}$")


class SyncStart(BaseModel):
    sync_id: str = Field(min_length=1, max_length=64)
    mode: Literal["full", "incremental"]


class SyncRef(BaseModel):
    sync_id: str = Field(min_length=1, max_length=64)


class Manifest(BaseModel):
    sync_id: str = Field(min_length=1, max_length=64)
    entries: dict[str, str]


class CoursesBody(BaseModel):
    sync_id: str | None = None
    courses: list[CourseIn]


class CourseIds(BaseModel):
    course_ids: list[int]


class DocumentsBody(BaseModel):
    sync_id: str | None = None
    documents: list[DocumentIn] = Field(max_length=500)


class DocIds(BaseModel):
    doc_ids: list[str]


def _conflict(exc: SyncStateError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/sync/start")
async def sync_start(body: SyncStart, site: SiteDep, store: StoreDep) -> dict[str, str]:
    try:
        store.start_sync(site.site_id, body.sync_id, mode=body.mode)
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    return {"sync_id": body.sync_id}


@router.post("/manifest")
async def manifest(body: Manifest, site: SiteDep, store: StoreDep) -> dict[str, list[str]]:
    try:
        need = store.apply_manifest(site.site_id, body.sync_id, body.entries)
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    return {"need": need}


@router.post("/sync/finish")
async def sync_finish(body: SyncRef, site: SiteDep, store: StoreDep) -> SyncResult:
    try:
        return store.finish_sync(site.site_id, body.sync_id)
    except SyncStateError as exc:
        raise _conflict(exc) from exc


@router.post("/courses")
async def courses(body: CoursesBody, site: SiteDep, store: StoreDep) -> dict[str, int]:
    store.upsert_courses(site.site_id, body.courses, sync_id=body.sync_id)
    return {"count": len(body.courses)}


@router.post("/courses/delete")
async def delete_courses(body: CourseIds, site: SiteDep, store: StoreDep) -> dict[str, int]:
    gone = store.tombstone_courses(site.site_id, body.course_ids)
    return {"documents_removed": len(gone)}


@router.post("/documents")
async def documents(
    body: DocumentsBody, request: Request, site: SiteDep, store: StoreDep
) -> dict[str, list[str]]:
    changed = store.upsert_documents(site.site_id, body.documents, sync_id=body.sync_id)
    needs_blob = store.missing_blobs(d.file.sha256 for d in body.documents if d.file)
    if changed:
        for listener in request.app.state.document_listeners:
            listener(site.site_id, changed)
    return {"changed": changed, "needs_blob": needs_blob}


@router.post("/documents/delete")
async def delete_documents(body: DocIds, site: SiteDep, store: StoreDep) -> dict[str, int]:
    store.tombstone_documents(site.site_id, body.doc_ids)
    return {"count": len(body.doc_ids)}


@router.put("/blobs/{sha256}", response_model=None)
async def put_blob(
    request: Request,
    site: SiteDep,
    store: StoreDep,
    sha256: str = SHA256_PATH,
) -> JSONResponse:
    if not store.blob_referenced(site.site_id, sha256):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no document references this blob")
    if store.blob_exists(sha256):
        return JSONResponse({"sha256": sha256, "stored": False}, status_code=200)
    limit = request.app.state.settings.pipeline.max_file_bytes
    writer = store.blob_writer(sha256, max_bytes=limit)
    try:
        async for chunk in request.stream():
            writer.write(chunk)
        writer.commit()
    except StoreError as exc:
        message = str(exc)
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "too large" in message
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, detail=message) from exc
    except BaseException:
        writer.abort()
        raise
    for listener in request.app.state.blob_listeners:
        listener(site.site_id, sha256)
    return JSONResponse({"sha256": sha256, "stored": True}, status_code=201)


@router.get("/status")
async def ingest_status(request: Request, site: SiteDep, store: StoreDep) -> dict[str, Any]:
    result = store.status(site.site_id)
    pipeline = getattr(request.app.state, "pipeline_status", None)
    if pipeline is not None:
        result["pipeline"] = pipeline()
    return result
