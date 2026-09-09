"""Request/response shapes for `api`'s internal endpoints — cron and matrix are
the only callers (never the public internet). See specs/015-microservice-split.md.
"""

from __future__ import annotations

from pydantic import BaseModel

from bsbot.index.store import Document, FetchRecord
from bsbot.ingest.model import ContentItem


class CrawlResultRequest(BaseModel):
    items: list[ContentItem]


class CrawlResultResponse(BaseModel):
    persisted: int
    pending: list[Document]


class FetchRecordResponse(BaseModel):
    record: FetchRecord | None
    blob_exists: bool


class RecordFetchRequest(BaseModel):
    url: str
    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    moodle_timemodified: int | None = None
    fetched_at: int | None = None
    checked_at: int | None = None


class TouchFetchRequest(BaseModel):
    url: str
    checked_at: int


class FetchFailureRequest(BaseModel):
    url: str
    error: str
    checked_at: int | None = None


class BlobResponse(BaseModel):
    sha256: str


class SegmentDTO(BaseModel):
    text: str
    page: int | None = None
    label: str | None = None
    meta: dict[str, object] = {}


class SegmentsRequest(BaseModel):
    segments: list[SegmentDTO] | None = None
    blob_sha256: str | None = None


class SegmentsResponse(BaseModel):
    indexed: int
    chunks: int
    skipped: int
    failed: int


class EmbedPendingResponse(BaseModel):
    embedded: int
    total: int


class IngestMessageRequest(BaseModel):
    room_id: str
    event_id: str
    sender: str
    text: str
    timemodified: int
    room_name: str = ""
    max_history_per_room: int = 10


class IngestMessageResponse(BaseModel):
    chunk_ids: list[int]
