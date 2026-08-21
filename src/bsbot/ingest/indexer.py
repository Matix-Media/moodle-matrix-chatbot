"""Ingest pipeline: fetch -> extract -> chunk -> store.

Only documents the manifest reports as pending are touched, so a routine run after
a no-op sync does nothing at all. Failures are per-document: one corrupt PDF or one
dead link must never cost the rest of the batch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import structlog

from bsbot.index.aliases import alias_text
from bsbot.index.store import Document, Store, content_sha256
from bsbot.ingest.chunk import (
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_TARGET_CHARS,
    Segment,
    chunk_segments,
)
from bsbot.ingest.extract import EXTRACT_VERSION, ExtractionError, OcrCallable, extract
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

#: filename hints so the generic extractor dispatches correctly for a fetched
#: export (magic bytes cover pptx/xlsx; a plain-text export has no signature).
_GOOGLE_FILENAME_HINTS = {"text": "export.txt", "pptx": "export.pptx", "xlsx": "export.xlsx"}

TaskcardsFetch = Callable[
    ..., Awaitable[dict[str, Any] | None]
]  # (http, host, board_id, *, share_token=...)

log = structlog.get_logger(__name__)


class FetcherLike(Protocol):
    async def fetch(self, url: str, *, moodle_timemodified: int | None = None) -> FetchResult: ...


@dataclass
class IndexStats:
    indexed: int = 0
    chunks: int = 0
    skipped: int = 0
    failed: int = 0


class Indexer:
    def __init__(
        self,
        store: Store,
        fetcher: FetcherLike,
        *,
        ocr: OcrCallable | None = None,
        extract_version: int = EXTRACT_VERSION,
        target_chars: int = DEFAULT_TARGET_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
        aliases: dict[str, list[str]] | None = None,
        moodle_host: str | None = None,
        youtube_api: TranscriptApi | None = None,
        taskcards_fetch: TaskcardsFetch | None = None,
    ) -> None:
        self._store = store
        self._fetcher = fetcher
        self._ocr = ocr
        self._extract_version = extract_version
        self._target = target_chars
        self._overlap = overlap_chars
        #: doc_id -> human-supplied search phrases (spec 007 AC-19/AC-20). See
        #: bsbot.index.aliases for why this exists: institutional naming no
        #: retrieval mechanism can discover from the text alone.
        self._aliases = aliases or {}
        #: Needed to recognise a same-host pluginfile.php link as already-a-file
        #: (spec 011 AC-15), rather than trying to resolve it as some other kind
        #: of external content.
        self._moodle_host = moodle_host
        self._youtube_api = youtube_api
        self._taskcards_fetch: TaskcardsFetch = taskcards_fetch or fetch_board

    async def index_pending(self, *, limit: int | None = None) -> IndexStats:
        pending = self._store.documents_needing_extraction(extract_version=self._extract_version)
        if limit is not None:
            pending = pending[:limit]

        stats = IndexStats()
        for document in pending:
            try:
                await self._index_one(document, stats)
            except Exception as exc:  # never let one document abort the batch
                stats.failed += 1
                log.warning(
                    "index.failed",
                    doc_id=document.doc_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
        log.info(
            "index.done",
            indexed=stats.indexed,
            chunks=stats.chunks,
            skipped=stats.skipped,
            failed=stats.failed,
        )
        return stats

    async def _index_one(self, document: Document, stats: IndexStats) -> None:
        segments, blob_sha = await self._segments_for(document, stats)
        aliases = self._aliases.get(document.doc_id)

        # A document with an alias but no extractable content (a scan awaiting OCR,
        # say) must still get its alias chunk — the alias is precisely what makes it
        # findable in the meantime.
        if segments is None and aliases is None:
            return
        chunks = (
            chunk_segments(
                segments,
                header_path=document.header_path,
                target_chars=self._target,
                overlap_chars=self._overlap,
            )
            if segments is not None
            else []
        )

        if aliases:
            chunks.extend(
                chunk_segments(
                    [Segment(text=alias_text(aliases))], header_path=document.header_path
                )
            )

        if not chunks:
            stats.skipped += 1
            self._store.record_extraction(
                document.doc_id,
                text_sha256="",
                extract_version=self._extract_version,
                blob_sha256=blob_sha,
            )
            return

        text_sha256 = content_sha256("\n".join(c.body for c in chunks))
        # External content (a live TaskCards board, a HackMD note) is re-fetched on
        # a schedule we don't control the granularity of, so re-extraction happens
        # far more often than the text actually changes. Comparing hashes here is
        # what keeps a no-op refresh from re-embedding a whole document: replacing
        # chunks always assigns them fresh ids, and the embedder treats a fresh id
        # as new work regardless of whether its text is identical to before.
        if text_sha256 == document.text_sha256:
            stats.skipped += 1
            self._store.record_extraction(
                document.doc_id,
                text_sha256=text_sha256,
                extract_version=self._extract_version,
                blob_sha256=blob_sha,
            )
            return

        self._store.replace_chunks(
            document.doc_id,
            [
                (
                    chunk.text,
                    {
                        "ordinal": chunk.ordinal,
                        "page": chunk.page,
                        "header_text": chunk.header_text,
                        "body": chunk.body,
                    },
                )
                for chunk in chunks
            ],
            header_text=chunks[0].header_text,
        )
        self._store.record_extraction(
            document.doc_id,
            text_sha256=text_sha256,
            extract_version=self._extract_version,
            blob_sha256=blob_sha,
        )
        stats.indexed += 1
        stats.chunks += len(chunks)

    async def _segments_for(
        self, document: Document, stats: IndexStats
    ) -> tuple[list[Segment] | None, str | None]:
        # Inline text (labels, module intros) needs no network at all.
        if document.text:
            return [Segment(text=document.text)], None

        # YouTube and TaskCards produce text directly (a transcript, a rendered
        # board) rather than bytes to run through the generic extractor, so they
        # are handled before the generic fetch-and-extract path below.
        if document.external_url:
            video_id = extract_video_id(document.external_url)
            if video_id is not None:
                return await self._youtube_segments(document, video_id, stats)
            if is_taskcards_url(document.external_url):
                return await self._taskcards_segments(document, stats)

        url, filename = self._target_url(document)
        if url is None:
            stats.skipped += 1
            return None, None

        result = await self._fetcher.fetch(url, moodle_timemodified=document.timemodified)
        if result.sha256 is None:
            stats.failed += 1
            log.warning("index.fetch_failed", doc_id=document.doc_id, error=result.error)
            return None, None

        data = self._store.blob_path(result.sha256).read_bytes()
        try:
            segments = extract(data, filename=result.filename or filename, ocr=self._ocr)
        except ExtractionError as exc:
            stats.failed += 1
            log.warning("index.extract_failed", doc_id=document.doc_id, error=str(exc))
            return None, None
        return segments, result.sha256

    async def _youtube_segments(
        self, document: Document, video_id: str, stats: IndexStats
    ) -> tuple[list[Segment] | None, str | None]:
        """Fetch a transcript in a worker thread — the library is blocking."""
        api = self._youtube_api
        if api is None:
            from youtube_transcript_api import YouTubeTranscriptApi

            api = YouTubeTranscriptApi()
        text = await asyncio.to_thread(fetch_transcript, video_id, api=api)
        if text is None:
            stats.skipped += 1
            return None, None
        return [Segment(text=text)], None

    async def _taskcards_segments(
        self, document: Document, stats: IndexStats
    ) -> tuple[list[Segment] | None, str | None]:
        assert document.external_url is not None
        host = urlsplit(document.external_url).netloc
        board_id = board_id_from_url(document.external_url)
        if board_id is None:
            stats.skipped += 1
            return None, None
        # The URL's own ?token= must be redeemed for a private board — found live,
        # see bsbot.ingest.taskcards.fetch_board's docstring.
        share_token = share_token_from_url(document.external_url)
        async with httpx.AsyncClient(timeout=20) as http:
            board = await self._taskcards_fetch(http, host, board_id, share_token=share_token)
        if board is None:
            stats.failed += 1
            log.warning("index.taskcards_failed", doc_id=document.doc_id, board_id=board_id)
            return None, None
        return [Segment(text=render_board_text(board))], None

    def _target_url(self, document: Document) -> tuple[str | None, str]:
        """Resolve what to fetch, trying each known adapter in turn."""
        if document.file_url:
            return document.file_url, document.filename or "unknown"
        if not document.external_url:
            return None, ""
        url = document.external_url

        if self._moodle_host and self._is_pluginfile_link(url):
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

    def _is_pluginfile_link(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.netloc == self._moodle_host and "/pluginfile.php/" in parts.path
