"""Data shapes: what the Moodle plugin pushes (``*In``) and what the Store returns."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

_SHA256_HEX = r"^[0-9a-f]{64}$"


class DocumentKind(StrEnum):
    #: Text sent inline by the plugin (module intro, label).
    INLINE = "inline"
    #: HTML sent inline (page content, book chapter).
    HTML = "html"
    #: A file whose bytes are uploaded separately as a blob.
    FILE = "file"
    #: An external URL the API fetches itself (YouTube, HackMD, TaskCards, …).
    EXTERNAL = "external"


class FileRef(BaseModel):
    sha256: str = Field(pattern=_SHA256_HEX)
    filename: str
    mimetype: str | None = None
    filesize: int = Field(default=0, ge=0)


class CourseIn(BaseModel):
    course_id: int
    fullname: str
    shortname: str
    visible: bool = True
    startdate: int = 0
    enddate: int = 0
    timemodified: int = 0


class DocumentIn(BaseModel):
    """One answerable unit of course content, as the plugin builds it.

    ``doc_id`` is ``"{course_id}:{cmid}:{part}"``. ``content_hash`` is computed by the
    plugin over everything that affects the text (metadata, inline text, file hash),
    so an unchanged document is skipped without re-extraction.
    """

    doc_id: str = Field(min_length=1, max_length=255)
    course_id: int
    section_id: int = 0
    section_name: str = ""
    section_order: int = 0
    cmid: int
    modname: str = ""
    module_name: str = ""
    title: str = ""
    kind: DocumentKind
    header_path: list[str] = Field(default_factory=list)
    module_url: str | None = None
    timemodified: int = 0
    text: str | None = None
    file: FileRef | None = None
    external_url: str | None = None
    content_hash: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> DocumentIn:
        if self.kind is DocumentKind.FILE and self.file is None:
            raise ValueError("a 'file' document needs a file reference")
        if self.kind in (DocumentKind.INLINE, DocumentKind.HTML) and self.text is None:
            raise ValueError(f"an {self.kind.value!r} document needs text")
        if self.kind is DocumentKind.EXTERNAL and not self.external_url:
            raise ValueError("an 'external' document needs external_url")
        return self


class SegmentIn(BaseModel):
    """A unit of extracted text — a PDF page, a slide, a sheet, or a whole document."""

    ordinal: int
    text: str
    page: int | None = None
    label: str | None = None


class ChunkIn(BaseModel):
    ordinal: int
    text: str
    header_text: str = ""
    page: int | None = None


class Site(BaseModel):
    site_id: str
    name: str
    url: str
    created_at: int
    revoked_at: int | None = None


class Course(BaseModel):
    site_id: str
    course_id: int
    fullname: str
    shortname: str
    visible: bool
    startdate: int
    enddate: int
    timemodified: int


class Document(BaseModel):
    site_id: str
    doc_id: str
    course_id: int
    section_id: int
    section_name: str
    section_order: int
    cmid: int
    modname: str
    module_name: str
    title: str
    kind: DocumentKind
    header_path: list[str]
    module_url: str | None
    external_url: str | None
    text: str | None
    filename: str | None
    mimetype: str | None
    filesize: int
    blob_sha256: str | None
    timemodified: int
    content_hash: str
    extract_version: int | None
    extracted_at: int | None
    skip_reason: str | None
    char_count: int
    page_count: int
    first_seen: int
    last_seen: int
    tombstoned_at: int | None

    @property
    def needs_extraction(self) -> bool:
        return self.extract_version is None

    @property
    def header_text(self) -> str:
        return " › ".join(self.header_path)


class Segment(BaseModel):
    doc_id: str
    ordinal: int
    text: str
    page: int | None
    label: str | None


class Chunk(BaseModel):
    chunk_id: int
    site_id: str
    doc_id: str
    ordinal: int
    text: str
    header_text: str
    page: int | None


class SyncResult(BaseModel):
    sync_id: str
    mode: str
    tombstoned: list[str]
    courses_tombstoned: list[int]


class Job(BaseModel):
    id: int
    site_id: str
    doc_id: str
    stage: str
    state: str
    priority: int
    attempts: int
    next_attempt_at: int
    last_error: str | None


class Conversation(BaseModel):
    id: str
    site_id: str
    user_id: int
    course_id: int | None
    title: str
    created_at: int
    updated_at: int


class Turn(BaseModel):
    turn_id: str
    conversation_id: str
    state: str
    created_at: int
    finished_at: int | None
