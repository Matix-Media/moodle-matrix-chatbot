"""Fetch the content behind an external URL module.

Only known document hosts are fetched (YouTube transcripts, TaskCards boards, HackMD
notes, Google Docs/Slides/Sheets exports, Nextcloud shares); arbitrary web pages are
not crawled. Moodle-hosted files never come through here — the plugin uploads those.

Errors are classified so the pipeline knows whether to retry:
:class:`TransientFetchError` (network, 5xx, 429) or :class:`PermanentFetchError`
(404, oversized, unsupported).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from moodle_llm_chat.ingest.google_docs import GoogleDocsUnsupported, google_export_url
from moodle_llm_chat.ingest.hackmd import HackmdUnsupported, hackmd_markdown_url
from moodle_llm_chat.ingest.nextcloud import ShareUnsupported, share_download_url
from moodle_llm_chat.ingest.taskcards import (
    board_id_from_url,
    fetch_board,
    is_taskcards_url,
    render_board_text,
    share_token_from_url,
)
from moodle_llm_chat.ingest.youtube import TranscriptApi, extract_video_id, fetch_transcript

USER_AGENT = "moodle-llm-chat/0.2 (+course assistant indexer)"
_GOOGLE_FILENAMES = {"text": "export.txt", "pptx": "export.pptx", "xlsx": "export.xlsx"}
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

TaskcardsFetch = Callable[..., Awaitable[dict[str, Any] | None]]


class TransientFetchError(RuntimeError):
    """Worth retrying later."""


class PermanentFetchError(RuntimeError):
    """Retrying will not help."""


@dataclass(frozen=True)
class FetchedText:
    text: str


@dataclass(frozen=True)
class FetchedBytes:
    data: bytes
    filename: str


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _byte_source(url: str, title: str) -> tuple[str, str] | None:
    """(download URL, filename hint) for byte-producing adapters, or None."""
    try:
        return hackmd_markdown_url(url), "note.md"
    except HackmdUnsupported:
        pass
    try:
        export_url, kind = google_export_url(url)
        return export_url, _GOOGLE_FILENAMES[kind]
    except GoogleDocsUnsupported:
        pass
    try:
        return share_download_url(url), title or "download"
    except ShareUnsupported:
        return None


async def download(http: httpx.AsyncClient, url: str, *, max_bytes: int) -> bytes:
    try:
        async with http.stream(
            "GET", url, follow_redirects=True, headers={"User-Agent": USER_AGENT}, timeout=30.0
        ) as response:
            if response.status_code in _RETRYABLE_STATUS:
                raise TransientFetchError(f"HTTP {response.status_code}")
            if response.status_code >= 400:
                raise PermanentFetchError(f"HTTP {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise PermanentFetchError(f"download too large (limit {max_bytes} bytes)")
            return bytes(body)
    except httpx.TransportError as exc:
        raise TransientFetchError(f"{type(exc).__name__}: {exc}") from exc


async def fetch_external(
    url: str,
    *,
    title: str,
    http: httpx.AsyncClient,
    max_bytes: int,
    youtube_api: TranscriptApi | None = None,
    taskcards_fetch: TaskcardsFetch | None = None,
) -> FetchedText | FetchedBytes | None:
    """Fetch an external URL; ``None`` means "nothing indexable here" (a clean skip)."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise PermanentFetchError(f"unsupported URL scheme: {url!r}")

    video_id = extract_video_id(url)
    if video_id is not None:
        api = youtube_api
        if api is None:
            from youtube_transcript_api import YouTubeTranscriptApi

            api = YouTubeTranscriptApi()
        text = await asyncio.to_thread(fetch_transcript, video_id, api=api)
        return FetchedText(text=text) if text else None

    if is_taskcards_url(url):
        board_id = board_id_from_url(url)
        if board_id is None:
            return None
        board = await (taskcards_fetch or fetch_board)(
            http, host_of(url), board_id, share_token=share_token_from_url(url)
        )
        if board is None:
            raise TransientFetchError("taskcards board could not be fetched")
        return FetchedText(text=render_board_text(board))

    source = _byte_source(url, title)
    if source is None:
        return None
    download_url, filename = source
    return FetchedBytes(
        data=await download(http, download_url, max_bytes=max_bytes), filename=filename
    )
