# 007 — Embeddings and hybrid retrieval

- **Status:** active
- **Tests:** `tests/unit/test_embed.py`, `tests/unit/test_search.py`, `tests/unit/test_aliases.py`

## Goal

Answer a German question by finding the right chunks. Pure vector search is the obvious
choice and the wrong one here; see the plan's reasoning. The two retrievers fail in
*complementary* ways, which is exactly when fusion beats either alone:

- Questions are full of **lexical needles** — `LF05`, `IHK`, a teacher's surname, `15.03.2026`.
  Embeddings blur these; BM25 nails them.
- Questions also use **casual phrasing** against formally-worded material
  ("wann is die prüfung" vs "Termin der Abschlussprüfung Teil 1"). BM25 misses that;
  embeddings handle it.

## Acceptance criteria

### Embeddings
- `AC-1` Chunks embed with `task_type=RETRIEVAL_DOCUMENT`, queries with `RETRIEVAL_QUERY`.
  Using the same task type for both measurably degrades retrieval.
- `AC-2` Vectors are **normalised before storage** — `gemini-embedding-001` returns
  un-normalised vectors at reduced dimensionality (measured norm ≈ 0.58 at 768 dims).
- `AC-3` Embeddings are batched, respecting the API's 250-inputs / 20k-token limits.
- `AC-4` Requests are rate-limited below the free tier's 100 RPM.
- `AC-5` Embeddings are cached by `sha256(text) + model + dim + task_type`; re-indexing
  unchanged text costs nothing.
- `AC-6` A partial batch failure does not lose the successful part of the batch.

### Hybrid search
- `AC-7` Keyword search uses FTS5/BM25; vector search uses `vec0` cosine.
- `AC-8` Results are fused with Reciprocal Rank Fusion, `score = Σ 1/(k + rank)`, k=60.
- `AC-9` A document found by only one retriever still ranks; fusion never requires agreement.
- `AC-10` A chunk ranked highly by *both* retrievers outranks one found by a single retriever.
- `AC-11` Tombstoned documents never appear in results.
- `AC-12` User input is escaped for FTS5: a question containing `"`, `*`, `AND` or `NEAR`
  must not raise a syntax error or change the query's meaning.
- `AC-13` German umlaut folding works both ways: `Prufung` finds `Prüfung` and vice versa.
- `AC-14` Search degrades gracefully to keyword-only when embeddings are unavailable
  (no API key, quota exhausted), rather than returning nothing.
- `AC-15` Results carry enough provenance to cite: course, breadcrumb, module URL, page.

### Candidate pool and diversification

Found on the live corpus: a question naming "Lernfeld 10" returned a top-12 dominated by
three near-duplicate chunks of one LF4 file, none of them the right Lernfeld, while the
actually-relevant LF10 document — whose breadcrumb literally contained "Lernfeld 10" — never
entered the fused candidate set at all, because each query variant was independently cut to
12 results *before* cross-query fusion ran.

- `AC-16` Each query variant is retrieved at a wider depth than the final candidate count
  before cross-query fusion runs, so a document that ranks just outside the top-N for every
  individual query can still surface once its scores are combined.
- `AC-17` The final candidate list caps how many chunks may come from the same document,
  so near-duplicate chunks of one file cannot crowd out a different, relevant document.
- `AC-18` A Lernfeld/module number named explicitly in the question (`LF10`, `Lernfeld 10`,
  `LF 06`, ...) boosts candidates whose breadcrumb names the same Lernfeld above all others,
  using the breadcrumb signal the crawler already captures — no schema change, no re-crawl.

### Institutional-knowledge aliases

Some documents are named after context with zero lexical or semantic connection to what a
student would search for — a Lernfeld 10 grading sheet titled "Bewertung Barcamp" because the
LF10 project (a Design Pattern workshop) is presented at an event called a Barcamp. No
retrieval mechanism can discover that association from the text; it has to be told, the same
way a teacher eventually tells a student who asks.

- `AC-19` A human-maintained alias file maps a `doc_id` to search phrases a student would
  plausibly use, editable without touching code.
- `AC-20` An aliased document's search phrases are indexed as their own retrievable unit,
  so both BM25 and the embedding reach them, without diluting the document's own chunks.
- `AC-21` A missing or absent alias file is not an error — most deployments will not have one.
- `AC-22` Editing the alias file does not require a full re-index: a targeted command
  re-processes only the documents named in the file.

## Non-goals

- Answer generation and reranking are spec 008.
