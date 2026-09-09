"""Ingest pipeline: fetch -> extract -> chunk -> store.

Only documents the manifest reports as pending are touched, so a routine run after
a no-op sync does nothing at all. Failures are per-document: one corrupt PDF or one
dead link must never cost the rest of the batch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

import structlog

from bsbot.index.aliases import alias_text
from bsbot.index.store import Document, Store, content_sha256
from bsbot.ingest.chunk import (
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_TARGET_CHARS,
    Segment,
    chunk_segments,
)
from bsbot.ingest.extract import EXTRACT_VERSION, OcrCallable
from bsbot.ingest.segments import FetcherLike, TaskcardsFetch, resolve_segments
from bsbot.ingest.youtube import TranscriptApi
from bsbot.pii.tokenizer import PiiTokenizer
from bsbot.rag.prompts import CONTEXTUAL_CHUNK_TEMPLATE, DOCUMENT_SUMMARY_TEMPLATE, HYPE_TEMPLATE

HypeGenerator = Callable[[str], Awaitable[list[str]] | list[str]]
SummaryGenerator = Callable[[str, str], Awaitable[str | None] | str | None]
#: (full document text, this chunk's own body) -> a 1-2 sentence situating context.
ContextGenerator = Callable[[str, str], Awaitable[str | None] | str | None]

log = structlog.get_logger(__name__)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


class LLMLike(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = ...,
        model: str | None = ...,
        temperature: float = ...,
        purpose: str = ...,
    ) -> str: ...


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
        llm: LLMLike | None = None,
        utility_model: str | None = None,
        extract_version: int = EXTRACT_VERSION,
        target_chars: int = DEFAULT_TARGET_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
        aliases: dict[str, list[str]] | None = None,
        moodle_host: str | None = None,
        youtube_api: TranscriptApi | None = None,
        taskcards_fetch: TaskcardsFetch | None = None,
        semantic: bool = False,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
        hype: bool = False,
        hype_generator: HypeGenerator | None = None,
        summarize: bool = False,
        summary_generator: SummaryGenerator | None = None,
        #: Contextual retrieval (Anthropic's published technique): prepend a short
        #: LLM-written sentence situating each chunk within its document (which
        #: week of a Blockplan, which Lernfeld) before it is embedded/indexed. A
        #: chunk's own text alone often can't say that — a Blockplan is one
        #: continuous document arbitrarily cut into fixed-size chunks (see
        #: DEFAULT_NEIGHBOR_RADIUS in rag/pipeline.py) — but full-corpus reindexing
        #: costs one LLM call per chunk, same order of cost as ``hype``.
        contextualize: bool = False,
        context_generator: ContextGenerator | None = None,
        pii_tokenizer: PiiTokenizer | None = None,
    ) -> None:
        self._store = store
        self._fetcher = fetcher
        self._ocr = ocr
        self._llm = llm
        self._utility_model = utility_model
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
        self._taskcards_fetch = taskcards_fetch
        self._semantic = semantic
        self._embedder = embedder
        self._hype = hype
        self._hype_generator = hype_generator
        self._summarize = summarize
        self._summary_generator = summary_generator
        self._contextualize = contextualize
        self._context_generator = context_generator
        self._pii_tokenizer = pii_tokenizer

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

    async def _generate_hype(self, text: str) -> list[str]:
        if not text.strip():
            return []
        if self._hype_generator is not None:
            res = self._hype_generator(text)
            if asyncio.iscoroutine(res):
                return await res
            return res  # type: ignore[return-value]
        if self._llm is not None:
            try:
                raw = await asyncio.to_thread(
                    self._llm.generate,
                    HYPE_TEMPLATE.format(text=_clip(text, 2000), n=3),
                    purpose="hype",
                    model=self._utility_model,
                    temperature=0.3,
                )
                return [line.strip(" -•\t") for line in raw.splitlines() if line.strip(" -•\t")]
            except Exception as exc:
                log.info("index.hype_failed", error=str(exc))
        return []

    async def _generate_summary(self, title: str, text: str) -> str | None:
        if not text.strip():
            return None
        if self._summary_generator is not None:
            res = self._summary_generator(title, text)
            if asyncio.iscoroutine(res):
                return await res
            return res  # type: ignore[return-value]
        if self._llm is not None:
            try:
                raw = await asyncio.to_thread(
                    self._llm.generate,
                    DOCUMENT_SUMMARY_TEMPLATE.format(title=title, text=_clip(text, 4000)),
                    purpose="summary",
                    model=self._utility_model,
                    temperature=0.2,
                )
                summary = raw.strip()
                return summary if summary else None
            except Exception as exc:
                log.info("index.summary_failed", error=str(exc))
        return None

    def _augmentation_signature(self) -> str:
        """Which per-chunk augmentations are active this run, as a stable string.

        Folded into the change-detection hash below `_index_one` so that toggling
        ``--hype``/``--summarize``/``--contextualize`` on for a document whose raw
        text is unchanged still triggers reprocessing. Without this, the hash
        compared only chunk *bodies* — which none of these three touch, they only
        add to the indexed text built after the hash check — so flipping one of
        these flags on for an already-extracted document spent the LLM calls and
        then silently discarded the result at the "unchanged" skip below.
        """
        return (
            f"hype={bool(self._hype or self._hype_generator)}"
            f",summarize={bool(self._summarize or self._summary_generator)}"
            f",contextualize={bool(self._contextualize or self._context_generator)}"
        )

    async def _generate_context(self, full_text: str, chunk_body: str) -> str | None:
        if not chunk_body.strip():
            return None
        if self._context_generator is not None:
            res = self._context_generator(full_text, chunk_body)
            if asyncio.iscoroutine(res):
                return await res
            return res  # type: ignore[return-value]
        if self._llm is not None:
            try:
                raw = await asyncio.to_thread(
                    self._llm.generate,
                    CONTEXTUAL_CHUNK_TEMPLATE.format(
                        document=_clip(full_text, 3000), chunk=_clip(chunk_body, 1500)
                    ),
                    purpose="contextualize",
                    model=self._utility_model,
                    temperature=0.0,
                )
                context = raw.strip()
                return context if context else None
            except Exception as exc:
                log.info("index.contextualize_failed", error=str(exc))
        return None

    async def _index_one(self, document: Document, stats: IndexStats) -> None:
        """Fetch+extract, then hand off to `index_segments` — split in two so the
        second half (chunk/PII/augment/store) is independently callable with
        segments that arrived over the wire instead of freshly fetched (`api`'s
        `/internal/documents/{doc_id}/segments`, called by `cron` — which runs
        this exact fetch+extract step itself, just with no `Store` at all, see
        `bsbot.ingest.segments.resolve_segments` and specs/019-microservice-split.md).
        """
        outcome = await resolve_segments(
            document,
            self._fetcher,
            ocr=self._ocr,
            moodle_host=self._moodle_host,
            youtube_api=self._youtube_api,
            taskcards_fetch=self._taskcards_fetch,
        )
        if outcome.skipped:
            stats.skipped += 1
        elif outcome.failed:
            stats.failed += 1

        # A document with an alias but no extractable content (a scan awaiting OCR,
        # say) must still get its alias chunk — the alias is precisely what makes it
        # findable in the meantime. So a failed/skipped fetch only stops indexing
        # here if there is no alias either; otherwise index_segments(..., segments=
        # None, ...) still runs, and its own alias-chunk handling takes over.
        if outcome.segments is None and self._aliases.get(document.doc_id) is None:
            return
        await self.index_segments(document, outcome.segments, outcome.blob_sha256, stats)

    async def index_segments(
        self,
        document: Document,
        segments: list[Segment] | None,
        blob_sha: str | None,
        stats: IndexStats,
    ) -> None:
        """Chunk, PII-tokenize, augment (HyPE/summarize/contextualize) and store —
        the half of indexing that needs a `Store` (and, for the augmentation
        features, an LLM/embedder). Takes already-resolved segments rather than
        fetching them itself, so `api` can call this directly with segments `cron`
        sent over the wire.
        """
        from bsbot.ingest.chunk import Chunk

        aliases = self._aliases.get(document.doc_id)

        # A document with an alias but no extractable content (a scan awaiting OCR,
        # say) must still get its alias chunk — the alias is precisely what makes it
        # findable in the meantime.
        if segments is None and aliases is None:
            return

        # PII tokenization (spec 013): every segment, plus the course/section/module
        # breadcrumb, is tokenized before anything downstream — chunking, HyPE,
        # summarization, contextual retrieval, embedding — touches it. `document`
        # itself is never mutated, so `documents.text`/header fields stay raw for
        # local export.
        header_path = document.header_path
        title = document.title
        if self._pii_tokenizer is not None:
            if segments is not None:
                segments = [
                    replace(seg, text=self._pii_tokenizer.tokenize(seg.text)) for seg in segments
                ]
            header_path = [self._pii_tokenizer.tokenize(p) for p in document.header_path]
            title = self._pii_tokenizer.tokenize(document.title)

        chunks = (
            chunk_segments(
                segments,
                header_path=header_path,
                target_chars=self._target,
                overlap_chars=self._overlap,
                semantic=self._semantic,
                embedder=self._embedder,
            )
            if segments is not None
            else []
        )

        if aliases:
            chunks.extend(
                chunk_segments([Segment(text=alias_text(aliases))], header_path=header_path)
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

        # Hierarchical Indexing: generate document summary for multi-chunk documents
        if (self._summarize or self._summary_generator) and len(chunks) >= 2:
            full_text = "\n\n".join(c.body for c in chunks)
            summary_text = await self._generate_summary(title, full_text)
            if summary_text:
                header_text = " › ".join(p for p in header_path if p)
                summary_chunk = Chunk(
                    body=summary_text,
                    header_text=f"{header_text} › Zusammenfassung",
                    page=None,
                    ordinal=0,
                )
                chunks.insert(0, summary_chunk)

        # Re-assign ordinals
        for ordinal, chunk in enumerate(chunks):
            chunk.ordinal = ordinal

        # HyPE / Document Augmentation: generate hypothetical questions per chunk
        chunk_questions: dict[int, list[str]] = {}
        if self._hype or self._hype_generator:
            for idx, chunk in enumerate(chunks):
                qs = await self._generate_hype(chunk.body)
                if qs:
                    chunk_questions[idx] = qs

        # Contextual Retrieval: prepend a short situating sentence to each chunk's
        # own indexed text. Skips a summary chunk (inserted above) — it already is
        # the document-level context, contextualizing it would be circular.
        chunk_context: dict[int, str] = {}
        if self._contextualize or self._context_generator:
            full_text = "\n\n".join(
                c.body for c in chunks if "Zusammenfassung" not in c.header_text
            )
            for idx, chunk in enumerate(chunks):
                if "Zusammenfassung" in chunk.header_text:
                    continue
                ctx = await self._generate_context(full_text, chunk.body)
                if ctx:
                    chunk_context[idx] = ctx

        text_sha256 = content_sha256(
            "\n".join(c.body for c in chunks) + "|" + self._augmentation_signature()
        )
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

        formatted_chunks = []
        for idx, chunk in enumerate(chunks):
            qs = chunk_questions.get(idx, [])
            ctx = chunk_context.get(idx)
            # Situating context goes first (it frames the excerpt), hypothetical
            # questions last (they extend, rather than reframe, the searchable text).
            text_to_index = chunk.text
            if ctx:
                text_to_index = f"{ctx}\n\n{text_to_index}"
            if qs:
                text_to_index = f"{text_to_index}\n\nFragen:\n" + "\n".join(qs)

            meta: dict[str, Any] = {
                "ordinal": chunk.ordinal,
                "page": chunk.page,
                "header_text": chunk.header_text,
                "body": chunk.body,
            }
            if qs:
                meta["questions"] = qs
            if ctx:
                meta["context"] = ctx
            if "Zusammenfassung" in chunk.header_text:
                meta["summary"] = True

            formatted_chunks.append((text_to_index, meta))

        self._store.replace_chunks(
            document.doc_id,
            formatted_chunks,
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
