# 015 — Index-time retrieval augmentation

- **Status:** active
- **Tests:** `tests/unit/test_indexer.py`, `tests/unit/test_chunk.py`

## Goal

Beyond plain extraction and structural chunking (spec 006) and embedding a chunk's own text
(spec 007), `bsbot index` grew three optional, independently-toggleable passes that change what
gets embedded and how documents get split, aimed squarely at the corpus problems spec 007 and
012 already documented: lexical needles embeddings blur, and documents (a Blockplan, a grading
sheet) whose text alone doesn't say what a chunk needs to say to be found. This spec documents
`--hype`, `--summarize` and `--semantic`, closing the gap spec 006's own "Index-time
augmentation" addendum flagged but deliberately left open. `--contextualize` is spec 006
AC-25/AC-26 and is not repeated here except where the three below share its machinery.

## Acceptance criteria

### HyPE (Hypothetical Prompt Embeddings, `--hype`)
- `AC-1` An LLM (or an injectable generator, for testing) produces a small number of hypothetical
  questions per chunk, appended to that chunk's *indexed* text after a `Fragen:` marker — so a
  question phrased the way the generated ones are can match the chunk via FTS5/BM25 even when its
  literal wording is absent from the chunk's own body.
- `AC-2` A HyPE failure (quota, timeout, an empty generator result) drops the augmentation for
  that one chunk only; the chunk is still indexed with its original text. It never fails the
  document.

### Hierarchical summarization (`--summarize`)
- `AC-3` For a document that produced two or more chunks, one document-level summary is
  generated and inserted as its own chunk at ordinal 0, ahead of the document's real chunks
  (whose ordinals shift up to make room). Its header path is suffixed "› Zusammenfassung" and its
  metadata carries `summary: true`, so downstream consumers (query-time neighbour expansion,
  dated reranking) can recognise and exclude it from context that expects to be the document's
  own prose.
- `AC-4` A document producing exactly one chunk, or a summary generator that returns nothing,
  gets no summary chunk — there is nothing to hierarchically summarize, and an empty summary is
  worse than none.

### Semantic chunking (`--semantic`)
- `AC-5` With an embedder configured, sentence boundaries are grouped into chunks by cosine
  distance between consecutive sentence embeddings: a chunk boundary is placed where distance
  exceeds a breakpoint computed from the configured `breakpoint_type` (`percentile`,
  `standard_deviation`, `interquartile`, or `gradient`) and `breakpoint_amount` — chunk
  boundaries follow topic changes the embeddings detect, instead of only a fixed character
  target.
- `AC-6` The character target is still enforced as a hard cap within a topic: a single long
  run with no detected topic change does not grow past it unbounded.
- `AC-7` Without an embedder configured, semantic chunking falls back to plain structural
  `chunk_segments`, not an error.
- `AC-8` An embedder failure during semantic chunking (the embedding API is down) degrades to
  structural chunking for that document rather than failing it.

### Composition and change detection
- `AC-9` The three augmentations here and `--contextualize` (spec 006 AC-25) compose on the same
  chunk: situating context (if any) is prepended first, hypothetical questions (if any) are
  appended last, so the chunk's own body stays in the middle regardless of which augmentations
  are active.
- `AC-10` Enabling `--hype` or `--summarize` for a document already extracted without them
  reprocesses that document even though its raw text hasn't changed — the same
  augmentation-aware change-detection fix spec 006 AC-26 documents for `--contextualize` covers
  `--hype`/`--summarize` identically (they were the first two augmentations the bug affected,
  before `--contextualize` existed).

## Non-goals

- No independent caching of generated questions/summaries by their own content hash; a change in
  which augmentations are enabled reprocesses the whole document, not incrementally.
- The generated hypothetical questions are never shown to a user — they exist only to widen what
  a chunk can be found by, not as answer content.
- Chunk-boundary quality tuning (choice of `breakpoint_type`/`breakpoint_amount` defaults) is an
  ongoing benchmarking question, tracked in spec 012, not fixed by this spec.

## Notes

Builds on spec 006 (extraction/chunking) and spec 007 (embeddings, whose `embedder` callable
semantic chunking reuses). Benchmarked alongside spec 012's comparison of RAG techniques.
