"""Resolve a document's raw text segments: fetch + extract, no storage at all —
specs/003-006 and specs/011 (external adapters).

Split out of `Indexer` so it can run with only a `FetcherLike`, no `Store` — this
is the "cron fetches and extracts" half of fetch->extract->chunk->store; the other
half (chunk/PII/embed/store) runs wherever a `Store` lives (`Indexer.index_segments`,
called locally by the `bsbot index` dev tool or remotely by `api`'s
`/internal/documents/{doc_id}/segments` — see specs/015-microservice-split.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import structlog

from bsbot.index.store import Document
from bsbot.ingest.chunk import Segment
from bsbot.ingest.extract import ExtractionError, OcrCallable, extract
from bsbot.ingest.fetcher import FetchResult
from bsbot.ingest.google_docs import GoogleDocsUnsupported, google_export_url
from bsbot.ingest.hackmd import HackmdUnsupported, hackmd_markdown_url
from bsbot.ingest.nextcloud import ShareUnsupported, share_download_url
from bsbot.ingest.taskcards import (
    board_id_from_url,
    fetch_board,
    is_taskcards_url,
    render_board_text,
    share_token_from_url,
)
from bsbot.ingest.youtube import TranscriptApi, extract_video_id, fetch_transcript

log = structlog.get_logger(__name__)

_GOOGLE_FILENAME_HINTS = {"text": "export.txt", "pptx": "export.pptx", "xlsx": "export.xlsx"}

TaskcardsFetch = Callable[..., Awaitable[dict[str, Any] | None]]


class FetcherLike(Protocol):
    async def fetch(self, url: str, *, moodle_timemodified: int | None = None) -> FetchResult: ...


@dataclass
class SegmentsOutcome:
    segments: list[Segment] | None = None
    #: The blob backing this extraction, if any (inline text / YouTube / TaskCards
    #: have none — they never went through the byte-fetching path at all).
    blob_sha256: str | None = None
    skipped: bool = False
    failed: bool = False
    error: str | None = None


async def resolve_segments(
    document: Document,
    fetcher: FetcherLike,
    *,
    ocr: OcrCallable | None = None,
    moodle_host: str | None = None,
    youtube_api: TranscriptApi | None = None,
    taskcards_fetch: TaskcardsFetch | None = None,
) -> SegmentsOutcome:
    # Inline text (labels, module intros) needs no network at all.
    if document.text:
        return SegmentsOutcome(segments=[Segment(text=document.text)])

    # YouTube and TaskCards produce text directly (a transcript, a rendered
    # board) rather than bytes to run through the generic extractor, so they
    # are handled before the generic fetch-and-extract path below.
    if document.external_url:
        video_id = extract_video_id(document.external_url)
        if video_id is not None:
            return await _youtube_segments(video_id, youtube_api)
        if is_taskcards_url(document.external_url):
            return await _taskcards_segments(document, taskcards_fetch or fetch_board)

    url, filename = _target_url(document, moodle_host)
    if url is None:
        return SegmentsOutcome(skipped=True)

    result = await fetcher.fetch(url, moodle_timemodified=document.timemodified)
    if result.sha256 is None or result.data is None:
        log.warning("segments.fetch_failed", doc_id=document.doc_id, error=result.error)
        return SegmentsOutcome(failed=True, error=result.error)

    try:
        segments = extract(result.data, filename=result.filename or filename, ocr=ocr)
    except ExtractionError as exc:
        log.warning("segments.extract_failed", doc_id=document.doc_id, error=str(exc))
        return SegmentsOutcome(failed=True, error=str(exc))
    return SegmentsOutcome(segments=segments, blob_sha256=result.sha256)


async def _youtube_segments(video_id: str, youtube_api: TranscriptApi | None) -> SegmentsOutcome:
    """Fetch a transcript in a worker thread — the library is blocking."""
    api = youtube_api
    if api is None:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
    text = await asyncio.to_thread(fetch_transcript, video_id, api=api)
    if text is None:
        return SegmentsOutcome(skipped=True)
    return SegmentsOutcome(segments=[Segment(text=text)])


async def _taskcards_segments(
    document: Document, taskcards_fetch: TaskcardsFetch
) -> SegmentsOutcome:
    assert document.external_url is not None
    host = urlsplit(document.external_url).netloc
    board_id = board_id_from_url(document.external_url)
    if board_id is None:
        return SegmentsOutcome(skipped=True)
    # The URL's own ?token= must be redeemed for a private board — found live,
    # see bsbot.ingest.taskcards.fetch_board's docstring.
    share_token = share_token_from_url(document.external_url)
    async with httpx.AsyncClient(timeout=20) as http:
        board = await taskcards_fetch(http, host, board_id, share_token=share_token)
    if board is None:
        log.warning("segments.taskcards_failed", doc_id=document.doc_id, board_id=board_id)
        return SegmentsOutcome(failed=True, error="taskcards fetch failed")
    return SegmentsOutcome(segments=[Segment(text=render_board_text(board))])


def _target_url(document: Document, moodle_host: str | None) -> tuple[str | None, str]:
    """Resolve what to fetch, trying each known adapter in turn."""
    if document.file_url:
        return document.file_url, document.filename or "unknown"
    if not document.external_url:
        return None, ""
    url = document.external_url

    if moodle_host and _is_pluginfile_link(url, moodle_host):
        return url, url.rsplit("/", 1)[-1].split("?", 1)[0] or "download"
    try:
        return hackmd_markdown_url(url), "note.md"
    except HackmdUnsupported:
        pass
    try:
        export_url, kind = google_export_url(url)
        return export_url, _GOOGLE_FILENAME_HINTS[kind]
    except GoogleDocsUnsupported:
        pass
    try:
        return share_download_url(url), document.title or "download"
    except ShareUnsupported:
        return None, ""


def _is_pluginfile_link(url: str, moodle_host: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc == moodle_host and "/pluginfile.php/" in parts.path
