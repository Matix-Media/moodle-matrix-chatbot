# 013 — PII tokenization

- **Status:** draft
- **Tests:** `tests/unit/test_pii.py`

## Goal

The bot's only LLM/embedding provider is Google Gemini, a cloud API. Moodle content and live
Matrix chat messages routinely contain teacher/student names and email addresses as free text
(course names, page bodies, uploaded files, pasted contact info) — there is no structured
"participant" field, and today none of it is protected before being embedded or sent to Gemini
for generation.

This feature replaces person names and email addresses with stable, deterministic tokens before
any text reaches an embedding call, an LLM prompt, or the on-disk search index — while keeping
the system able to answer a question like "what's the teacher's email?" with the real value.
The real value is substituted back in locally, after the Gemini call returns, using a reversible
mapping that never leaves the machine. This is tokenization, not blind redaction: retrieval
still works because the same real-world entity always produces the same token, both when it was
indexed and when a student's question mentions it.

Off by default behind a config flag; enabling it on an existing deployment requires one
one-time reindex.

## Acceptance criteria

### Token format and normalization
- `AC-1` A person name and an email address each tokenize to the form
  `⟦PII<TYPE><12-hex-hash>⟧` where `<TYPE>` is `PERSON` or `EMAIL`.
- `AC-2` The same normalized entity always produces the same token — tokenizing is a pure
  function of `(entity_type, normalized_text)`, with no dependence on encounter order or prior
  state.
- `AC-3` An email address normalizes case-insensitively (`Teacher@Schule.DE` and
  `teacher@schule.de` tokenize identically).
- `AC-4` A person name normalizes case- and whitespace-insensitively (`Max Müller`,
  `MAX MÜLLER`, and `  Max   Müller  ` tokenize identically).
- `AC-5` Different surface forms of the same real person (`Herr Müller` vs `Max Müller`) are
  **not** merged — they tokenize to different tokens. This is a documented limitation, not a
  bug: there is no coreference resolution.
- `AC-6` Re-tokenizing already-tokenized text is a no-op (idempotent).
- `AC-7` Text with no detectable PII passes through `tokenize()` unchanged.

### Reversible mapping
- `AC-8` `detokenize()` restores every token produced by `tokenize()` back to the original
  surface form, via a store-backed mapping.
- `AC-9` A token-shaped string with no matching store entry is left as-is by `detokenize()`
  (never raises, never silently drops text).
- `AC-10` The first-seen real-world surface form of an entity is what `detokenize()` returns,
  even after the same normalized entity recurs later under different casing.
- `AC-11` The token↔value mapping is persisted in the existing index store (`pii_tokens` table),
  not a separate file, and survives process restarts and a full reindex.

### Ingest-time protection
- `AC-12` When a PII tokenizer is configured, an `Indexer` never calls the LLM's `generate()` or
  the embedder with text containing a detectable, untokenized name or email — covering inline
  page text, uploaded-file text, and the course/section/module breadcrumb used as embedding
  context and citation labels.
- `AC-13` Chunk text written to the store (`chunks.text`, `chunks_fts`) is the tokenized form;
  the source `documents.text` row is left untouched (raw), so local export is unaffected.
- `AC-14` A live Matrix moderator message indexed via `index_matrix_message` is tokenized before
  it is stored or embedded, the same as Moodle content. The Matrix sender ID itself is never
  tokenized — it is a protocol identifier, not free-text content.

### Query-time protection and answer detokenization
- `AC-15` A student's question is tokenized before it is used for query condensing,
  decomposition, expansion, step-back, embedding, reranking, relevance evaluation, or the final
  answer-generation prompt — every one of these sends text to Gemini.
- `AC-16` Retrieved-chunk metadata surfaced to the LLM prompt and to citations —
  `course_name`, `module_name`, `title` — is tokenized even though it is read from the raw
  `documents` table at query time, not only from pre-tokenized chunk text.
- `AC-17` The final `Answer.text` returned to the caller has every token resolved back to its
  real value.
- `AC-18` Every citation field that can carry a token (`title`, `course_name`, `header_text`) is
  resolved back to its real value in the returned `Answer.citations`.
- `AC-19` A local, non-Gemini operation that must reflect the student's literal wording — the
  Moodle-search fallback URL built when nothing is found — uses the original, untokenized
  question text, not the tokenized one.
- `AC-20` The follow-up-question suggestion call receives tokenized text, never the already
  -detokenized final answer — this call sends text to Gemini after the main answer is generated,
  so it must not regress into re-leaking resolved PII.

### Export
- `AC-21` Exporting a document whose markdown is sourced from inline text or from a re-extracted
  blob produces real (non-tokenized) values, since neither path touches the tokenized store.
- `AC-22` Exporting a document whose markdown is sourced from stored chunk text (the case where
  the source blob is unavailable) has every token resolved back to its real value before being
  written to disk.

### Configuration and rollout
- `AC-23` PII tokenization is off by default; no behavior changes for a deployment that does not
  opt in, and no new dependency (spaCy) is imported unless it is enabled.
- `AC-24` Enabling the flag on a deployment with an existing index does not require any manual
  deletion of chunks, vectors, or cache rows — a forced re-extraction pass alone produces a
  fully tokenized index, because changed content hashes make the existing "skip unchanged
  content" and cache-invalidation logic behave correctly on their own.

## Non-goals

- Phone numbers, postal addresses, and other identifiers are out of scope for this iteration.
- Image/OCR content is not protected — `transcribe_image`/`describe_image` send raw image bytes
  to Gemini directly; a name in a scanned document or photo is not caught by this text-level
  system.
- Coreference resolution across different surface forms of the same person (see AC-5).
- spaCy NER recall is not guaranteed — this is a probabilistic filter, not an absolute guarantee.
  The converse also holds and is not fully solved either: precision on short, context-free
  fragments is inherently limited (see `PiiTokenizer.tokenize_path` in Notes below).

## Notes

- Architecture: `documents` stays raw; only `chunks` (and the token↔value mapping) hold
  tokenized text. Tokenization happens once, in memory, immediately before text is handed to a
  chunker, an embedder, or an LLM prompt.
- Token delimiters `⟦`/`⟧` (U+27E6/U+27E7) do not collide with the existing `[QUELLE N]`
  citation syntax and are not expected to occur in Moodle content.
- Name detection uses a local, offline spaCy German NER model (`de_core_news_md` by default);
  email detection uses a regex. Both run entirely on the machine — no additional network calls.
- A breadcrumb (`header_path`, and `course_name`/`module_name`/`title` at query time) is
  tokenized via `PiiTokenizer.tokenize_path`, which joins the segments and runs NER once over the
  whole breadcrumb rather than once per segment. Confirmed against the real `de_core_news_md`
  model: a lone breadcrumb segment ("Bili-Team", "Klassenteam", even the plain word
  "Stundenplan") is frequently misclassified as `PER` when judged with no surrounding context —
  German capitalizes every noun, so the capitalization cue the model otherwise leans on carries no
  signal on a bare fragment. Reading the segment together with its siblings removes these false
  positives in every case checked, without losing real detections (a genuine name in a breadcrumb,
  e.g. "Frau Schmidt", is still caught). This is a mitigation, not a guarantee — precision on an
  isolated single-word segment can still be imperfect.
- Tokenization must happen before `content_sha256()` is computed for embedding/OCR cache keys,
  so those caches key off exactly what is actually sent to Gemini.
