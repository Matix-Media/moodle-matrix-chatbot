"""Structure-aware chunking — see ``specs/006-extraction-chunking.md``.

Two decisions matter for answer quality:

* **Every chunk is prefixed with its breadcrumb** (``Kurs › Abschnitt › Modul``).
  It gives the embedding model context a bare paragraph lacks, and it gives the
  answering model the provenance it needs to cite. The prefix is kept separate from
  ``body`` so the raw text is still available.
* **Splits follow structure**: paragraph, then sentence, then word. Cutting mid-sentence
  strands the subject of a sentence in one chunk and its object in another, which is
  exactly the content a retrieval query is looking for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TARGET_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 180

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?:;])\s+")


@dataclass
class Segment:
    """A unit of extracted text with optional page/slide provenance."""

    text: str
    page: int | None = None
    label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A retrieval unit."""

    body: str
    header_text: str
    page: int | None = None
    ordinal: int = 0

    @property
    def text(self) -> str:
        """What actually gets embedded and indexed."""
        return f"{self.header_text}\n\n{self.body}" if self.header_text else self.body


def normalise_text(text: str) -> str:
    """Collapse whitespace so identical content chunks identically (AC-18)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()


def chunk_segments(
    segments: list[Segment],
    *,
    header_path: list[str],
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split segments into overlapping chunks that respect structure."""
    header_text = " › ".join(p for p in header_path if p)
    chunks: list[Chunk] = []

    # Group consecutive segments so a chunk can span pages, remembering where it began.
    pieces: list[tuple[str, int | None]] = []
    for segment in segments:
        body = normalise_text(segment.text)
        if body:
            pieces.append((body, segment.page))
    if not pieces:
        return []

    buffer = ""
    buffer_page: int | None = None
    for body, page in pieces:
        if not buffer:
            buffer, buffer_page = body, page
        else:
            buffer = f"{buffer}\n\n{body}"

        while len(buffer) > target_chars:
            split_at = _find_split(buffer, target_chars)
            head, buffer = buffer[:split_at].strip(), buffer[split_at:].strip()
            if head:
                chunks.append(Chunk(body=head, header_text=header_text, page=buffer_page))
            if overlap_chars > 0 and head:
                buffer = f"{_tail(head, overlap_chars)} {buffer}".strip()
            buffer_page = page
    if buffer.strip():
        chunks.append(Chunk(body=buffer.strip(), header_text=header_text, page=buffer_page))

    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
    return chunks


def _find_split(text: str, target: int) -> int:
    """Best split point at or before ``target``: paragraph > sentence > word (AC-14).

    Falls back to ``target`` only when there is no boundary at all — a single
    unsplittable run such as a giant table row (AC-19).
    """
    window = text[: target + 1]

    paragraph = window.rfind("\n\n")
    if paragraph > target // 3:
        return paragraph

    sentence_ends = [m.start() for m in _SENTENCE_RE.finditer(window)]
    if sentence_ends and sentence_ends[-1] > target // 3:
        return sentence_ends[-1]

    space = window.rfind(" ")
    if space > target // 3:
        return space

    # No boundary: emit the run rather than cutting mid-token.
    following = text.find(" ", target)
    return following if following != -1 else len(text)


def _tail(text: str, size: int) -> str:
    """Trailing overlap, trimmed to a word boundary."""
    if len(text) <= size:
        return text
    tail = text[-size:]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail
