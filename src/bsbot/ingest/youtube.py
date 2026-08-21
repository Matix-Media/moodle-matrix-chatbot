"""YouTube transcripts — see ``specs/011-external-adapters.md``.

``youtube-transcript-api`` needs no API key for a public video's captions,
verified live against a real corpus video. For an assistant answering
Berufsschule questions, a linked explainer video's transcript is real content,
not a nice-to-have — worth the one new dependency.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import structlog

log = structlog.get_logger(__name__)

#: German first, then English, then whatever the video actually has (AC-8).
DEFAULT_LANGUAGES = ("de", "en")

_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})


def extract_video_id(url: str) -> str | None:
    """Pull the video id out of a watch or short-link URL (AC-7)."""
    parts = urlsplit(url)
    if parts.netloc in _YOUTUBE_HOSTS and parts.path == "/watch":
        values = parse_qs(parts.query).get("v")
        return values[0] if values else None
    if parts.netloc == "youtu.be":
        video_id = parts.path.strip("/")
        return video_id or None
    return None


class TranscriptApi(Protocol):
    def list(self, video_id: str) -> Any: ...


def fetch_transcript(
    video_id: str, *, api: TranscriptApi, languages: tuple[str, ...] = DEFAULT_LANGUAGES
) -> str | None:
    """Fetch a transcript, preferring ``languages`` in order, else anything available.

    Returns ``None`` rather than raising for a video with no captions at all
    (AC-9) — the caller treats that as a clean skip, not a failure.
    """
    try:
        transcript_list = api.list(video_id)
    except Exception as exc:
        log.info("youtube.no_transcripts", video_id=video_id, error=str(exc))
        return None

    try:
        transcript = transcript_list.find_transcript(list(languages))
    except Exception:
        try:
            transcript = next(iter(transcript_list))
        except StopIteration:
            log.info("youtube.no_transcripts", video_id=video_id)
            return None

    try:
        snippets = transcript.fetch()
    except Exception as exc:
        log.info("youtube.fetch_failed", video_id=video_id, error=str(exc))
        return None

    text = " ".join(s.text for s in snippets).strip()
    return text or None
