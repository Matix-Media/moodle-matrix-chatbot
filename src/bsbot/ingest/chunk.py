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

import math
import re
from collections.abc import Callable, Sequence
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


def split_sentences(text: str) -> list[str]:
    """Split text into sentences while preserving paragraphs and sentence structure."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: list[str] = []
    for p in paragraphs:
        splits = [s.strip() for s in _SENTENCE_RE.split(p) if s.strip()]
        sentences.extend(splits)
    return sentences


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    similarity = dot / (norm_a * norm_b)
    # Clamp cosine distance to [0, 2]
    return max(0.0, min(2.0, 1.0 - similarity))


def _calculate_breakpoint_threshold(
    distances: list[float],
    breakpoint_type: str = "percentile",
    breakpoint_amount: float = 90.0,
) -> float:
    """Calculate the threshold distance above which consecutive sentences form a chunk break."""
    if not distances:
        return 0.0
    sorted_dist = sorted(distances)
    n = len(sorted_dist)

    if breakpoint_type == "percentile":
        k = (breakpoint_amount / 100.0) * (n - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_dist[int(k)]
        d0 = sorted_dist[f] * (c - k)
        d1 = sorted_dist[c] * (k - f)
        return d0 + d1

    if breakpoint_type == "standard_deviation":
        mean = sum(distances) / n
        variance = sum((x - mean) ** 2 for x in distances) / n
        std = math.sqrt(variance)
        return mean + (breakpoint_amount * std)

    if breakpoint_type == "interquartile":
        q1_k = 0.25 * (n - 1)
        q3_k = 0.75 * (n - 1)
        q1 = sorted_dist[round(q1_k)]
        q3 = sorted_dist[round(q3_k)]
        iqr = q3 - q1
        return q3 + (breakpoint_amount * iqr)

    if breakpoint_type == "gradient":
        if n < 2:
            return sorted_dist[-1]
        grads = [abs(distances[i + 1] - distances[i]) for i in range(n - 1)]
        mean_grad = sum(grads) / len(grads)
        return mean_grad

    return sorted_dist[round(0.9 * (n - 1))]


def semantic_chunk_segments(
    segments: list[Segment],
    *,
    header_path: list[str],
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    breakpoint_type: str = "percentile",
    breakpoint_amount: float = 90.0,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Chunk segments semantically based on cosine distance between sentence embeddings.

    When an embedder is provided, sentences are embedded and chunk boundaries are
    placed at topic changes (drops in semantic similarity). If no embedder is provided,
    falls back to structural chunking.
    """
    if embedder is None:
        return chunk_segments(
            segments,
            header_path=header_path,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
        )

    header_text = " › ".join(p for p in header_path if p)
    # Collect sentences with page provenance
    all_sentences: list[tuple[str, int | None]] = []
    for segment in segments:
        body = normalise_text(segment.text)
        if not body:
            continue
        sents = split_sentences(body)
        for s in sents:
            all_sentences.append((s, segment.page))

    if not all_sentences:
        return []
    if len(all_sentences) == 1:
        return [
            Chunk(
                body=all_sentences[0][0],
                header_text=header_text,
                page=all_sentences[0][1],
                ordinal=0,
            )
        ]

    sentence_texts = [s[0] for s in all_sentences]
    try:
        embeddings = embedder(sentence_texts)
    except Exception:
        # Fallback to standard chunking if embedding fails
        return chunk_segments(
            segments,
            header_path=header_path,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
        )

    if len(embeddings) != len(sentence_texts):
        return chunk_segments(
            segments,
            header_path=header_path,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
        )

    # Compute distances between consecutive sentences
    distances = [
        _cosine_distance(embeddings[i], embeddings[i + 1])
        for i in range(len(embeddings) - 1)
    ]
    threshold = _calculate_breakpoint_threshold(distances, breakpoint_type, breakpoint_amount)

    # Group sentences into semantic chunks
    chunks: list[Chunk] = []
    current_sentences: list[str] = [all_sentences[0][0]]
    current_page = all_sentences[0][1]

    for i, dist in enumerate(distances):
        next_sent, next_page = all_sentences[i + 1]
        current_len = sum(len(s) for s in current_sentences) + len(current_sentences)

        # Break if semantic distance exceeds threshold or if chunk exceeds target_chars
        if dist > threshold or (current_len + len(next_sent) > target_chars):
            body_text = " ".join(current_sentences).strip()
            if body_text:
                chunks.append(Chunk(body=body_text, header_text=header_text, page=current_page))
            current_sentences = [next_sent]
            current_page = next_page
        else:
            current_sentences.append(next_sent)

    if current_sentences:
        body_text = " ".join(current_sentences).strip()
        if body_text:
            chunks.append(Chunk(body=body_text, header_text=header_text, page=current_page))

    for ordinal, chunk in enumerate(chunks):
        chunk.ordinal = ordinal
    return chunks


def chunk_segments(
    segments: list[Segment],
    *,
    header_path: list[str],
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    semantic: bool = False,
    embedder: Callable[[list[str]], list[list[float]]] | None = None,
    breakpoint_type: str = "percentile",
    breakpoint_amount: float = 90.0,
) -> list[Chunk]:
    """Split segments into overlapping chunks that respect structure."""
    if semantic and embedder is not None:
        return semantic_chunk_segments(
            segments,
            header_path=header_path,
            embedder=embedder,
            breakpoint_type=breakpoint_type,
            breakpoint_amount=breakpoint_amount,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
        )

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

