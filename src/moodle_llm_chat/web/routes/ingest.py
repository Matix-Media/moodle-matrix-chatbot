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
MAX_DOCUMENTS_PER_REQUEST = 500


class SyncStart(BaseModel):
    sync_id: str = Field(min_length=1, max_length=64)
    mode: Literal["full", "incremental"]


class SyncRef(BaseModel):
    sync_id: str = Field(min_length=1, max_length=64)


class ManifestEntry(BaseModel):
    doc_id: str
    content_hash: str
    timemodified: int | None = None
    #: Sent by plugins that hash placement; older ones omit it.
    placement_hash: str | None = None


class Manifest(BaseModel):
    """``entries`` as the plugin sends it (a list), or as a ``doc_id → hash`` map."""

    sync_id: str = Field(min_length=1, max_length=64)
    entries: list[ManifestEntry] | dict[str, str]
    chunk_no: int | None = None
    of: int | None = None

    def hashes(self) -> dict[str, str | tuple[str, str | None]]:
        if isinstance(self.entries, dict):
            return dict(self.entries)
        return {e.doc_id: (e.content_hash, e.placement_hash) for e in self.entries}


class CoursesBody(BaseModel):
    sync_id: str | None = None
    courses: list[CourseIn]


class CourseIds(BaseModel):
    course_ids: list[int]


class DocumentsBody(BaseModel):
    sync_id: str | None = None
    documents: list[DocumentIn]


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
        need = store.apply_manifest(site.site_id, body.sync_id, body.hashes())
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    return {"need": need}


@router.post("/sync/finish")
async def sync_finish(body: SyncRef, site: SiteDep, store: StoreDep) -> SyncResult:
    try:
        result = store.finish_sync(site.site_id, body.sync_id)
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    store.delete_orphan_blobs()
    return result


# Bodies are accepted in the plugin's shape (a bare JSON array) or wrapped in an object.


@router.post("/courses")
async def courses(
    body: list[CourseIn] | CoursesBody, site: SiteDep, store: StoreDep
) -> dict[str, int]:
    items, sync_id = (body, None) if isinstance(body, list) else (body.courses, body.sync_id)
    store.upsert_courses(site.site_id, items, sync_id=sync_id)
    return {"count": len(items)}


@router.post("/courses/delete")
async def delete_courses(body: CourseIds, site: SiteDep, store: StoreDep) -> dict[str, int]:
    gone = store.tombstone_courses(site.site_id, body.course_ids)
    return {"documents_removed": len(gone)}


@router.post("/documents")
async def documents(
    body: list[DocumentIn] | DocumentsBody, request: Request, site: SiteDep, store: StoreDep
) -> dict[str, list[str]]:
    items, sync_id = (body, None) if isinstance(body, list) else (body.documents, body.sync_id)
    if len(items) > MAX_DOCUMENTS_PER_REQUEST:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"at most {MAX_DOCUMENTS_PER_REQUEST} documents per request",
        )
    result = store.upsert_documents_detailed(site.site_id, items, sync_id=sync_id)
    needs_blob = store.missing_blobs(d.file.sha256 for d in items if d.file)
    if result.changed:
        # Pushes during an open full sync are bulk work; everything else is urgent.
        bulk = sync_id is not None or store.has_open_full_sync(site.site_id)
        for listener in request.app.state.document_listeners:
            listener(site.site_id, result.changed, bulk)
    if result.relocated:
        for relocated in request.app.state.relocation_listeners:
            relocated(site.site_id, result.relocated)
    return {"changed": result.changed, "needs_blob": needs_blob}


@router.post("/documents/delete")
async def delete_documents(
    body: list[str] | DocIds, site: SiteDep, store: StoreDep
) -> dict[str, int]:
    doc_ids = body if isinstance(body, list) else body.doc_ids
    store.tombstone_documents(site.site_id, doc_ids)
    return {"count": len(doc_ids)}


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
    result["site_id"] = site.site_id
    pipeline = getattr(request.app.state, "pipeline_status", None)
    if pipeline is not None:
        result["pipeline"] = pipeline()
    return result
