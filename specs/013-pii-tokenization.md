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
- `AC-4` A person name normalizes case-, whitespace- and diacritic-insensitively (`Max Müller`,
  `MAX MÜLLER`, `  Max   Müller  ` and `Max Muller` all tokenize identically). Folding
  diacritics matters because the token is what the embedder and the LLM see: without it a
  student who drops an umlaut — routine on a phone keyboard — names a different entity than the
  one the corpus recorded.
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
- `AC-13` Chunk text written to the store (`chunks.text`, `chunks.header_text`, `chunks_fts`,
  `meta["body"]`) is **raw**, and each has a tokenized twin — `chunks.text_tokenized`,
  `chunks.header_text_tokenized`, `meta["body_tokenized"]` — which is what may reach Gemini.
  Storing raw is what lets FTS5 do its job on names: its `remove_diacritics 2` folding and the
  `*` prefix matching in `fts5_escape` operate on real words, so `Muller` finds `Müller` and
  BM25 term overlap still links one surname across different documents. `documents.text` stays
  raw as before.
- `AC-14` A live Matrix moderator message indexed via `index_matrix_message` is stored raw with
  a tokenized twin, the same as Moodle content, and its inline embedding call is given the
  tokenized form. The Matrix sender ID itself is never tokenized — it is a protocol identifier,
  not free-text content.

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
  so it must not regress into re-leaking resolved PII. Because its input is tokenized, its
  output comes back in token space too, and `Answer.suggested_questions` is detokenized before
  it reaches the student — `_finalise` has already run by this point and never sees them.

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

### Egress boundary

The criteria above were written around "tokenize at ingest, so the index and everything
downstream of it is already safe". Auditing the live code found two problems with that
framing. It never actually protected the on-disk index — `pii_tokens` stores every entity's
plaintext `original` in the same SQLite file, so the decoder ring ships with the lockbox. And
it silently degraded name retrieval, because a hashed name defeats the FTS5 diacritic folding
(`unicode61 remove_diacritics 2`) and prefix matching this German corpus depends on: `Müller`
and `Muller` hash differently, and BM25 term overlap across documents disappears entirely. The
boundary that actually matters is egress to Gemini, so that is where the guarantee belongs.

- `AC-25` A call that sends text to Gemini from outside the answer pipeline is tokenized by its
  own caller. Specifically the benchmark judge: its golden question, expected keywords and
  answer text all reach Gemini after the pipeline's protection has ended, since `Answer.text`
  has already been detokenized by `_finalise` at that point.
- `AC-26` Tokenization by callers is backed by a guard at the egress boundary itself: every
  prompt reaching the LLM and every text reaching the embedding API is scrubbed against a
  gazetteer of already-known entities plus the email regex, and a non-zero catch is logged. The
  guard is a net, not a replacement — it can only find entities the system has already seen, so
  it never removes the caller's obligation to tokenize.
- `AC-27` The gazetteer contains only multi-word person names. A bare surname is frequently an
  ordinary German word (`Klein`, `Berg`, `Neu`) and the guard runs over whole formatted prompts,
  so single-token entries would corrupt unrelated text; `max müller` as a phrase carries no such
  risk.
- `AC-28` The gazetteer matches case- and diacritic-insensitively, so a question typed
  `wer ist max muller` is caught against a stored `Max Müller`.
- `AC-29` For the embedding path the scrub happens before the content hash and before the
  request log line, not merely before the API call — the cache must key off exactly what is
  sent (see Notes), and the `embed.request` log echoes the same strings.
- `AC-35` A chunk whose `text_tokenized` is `NULL` predates this migration and has no
  Gemini-facing form. Readers never fall back to the raw column: the embed passes skip such a
  row rather than embed it, and `_hydrate` tokenizes on the fly instead of returning raw text.
  `row["text_tokenized"] or row["text"]` is exactly the "trust the caller" mistake this boundary
  exists to remove.
- `AC-36` A `SearchHit` always carries the tokenized view. It is consumed directly by reranking,
  CRAG scoring, the follow-up hop and the answer prompt, none of which pass through any later
  tokenization step, so the raw columns never leave SQL.

### Prompt-facing aliases

Five stages — condensing, decomposition, expansion, step-back and the follow-up hop — ask the
model to rewrite a question and then use its answer *as a search query*. Asking it to carry
twelve hex characters through a generative rewrite is asking for a term that matches nothing:
one character of drift is enough, and dropping the token as noise is a likely outcome too. So
the model is never shown a token at all.

- `AC-30` A prompt sent to the LLM carries short aliases (`⟦PERSON_A⟧`, `⟦EMAIL_A⟧`) rather than
  `⟦PII…⟧` tokens, and the response is mapped back to real tokens before any caller sees it. The
  model therefore never has to reproduce a name or a hash, only a short label, and the real
  value is restored from a map it cannot corrupt.
- `AC-31` Alias suffixes are letters, never digits. `_reranked` and `_evaluate_relevance` parse
  numbers directly out of a raw response, and a `[0-9a-f]{12}` payload echoed into either one
  injects garbage indices or a garbage score. Both also strip entities before parsing, so a real
  token appearing in a response cannot corrupt them either.
- `AC-32` An alias in a response that this request never issued — invented or garbled by the
  model — is dropped rather than passed through. Rewrites are additive under RRF, so a query
  that lost its entity is merely weak, whereas a corrupted one matches nothing.
- `AC-33` Alias suffixes continue past 26 spreadsheet-style (`PERSON_AA`), since a single roster
  or attendance chunk can carry more than 26 names.
- `AC-34` The alias map is per-request, in-memory, and never consulted by `tokenize()` or
  `detokenize()`. It is a transport encoding for one round-trip, so AC-2's "pure function of
  `(entity_type, normalized_text)`" is unaffected. It is bound to a `ContextVar` rather than
  swapped onto the pipeline, which is a singleton shared across requests.

## Non-goals

- Phone numbers, postal addresses, and other identifiers are out of scope for this iteration.
- Image/OCR content is not protected — `transcribe_image`/`describe_image` send raw image bytes
  to Gemini directly; a name in a scanned document or photo is not caught by this text-level
  system.
- Coreference resolution across different surface forms of the same person (see AC-5).
- spaCy NER recall is not guaranteed — this is a probabilistic filter, not an absolute guarantee.

## Notes

- Architecture: `documents` stays raw; only `chunks` (and the token↔value mapping) hold
  tokenized text. Tokenization happens once, in memory, immediately before text is handed to a
  chunker, an embedder, or an LLM prompt.
- Token delimiters `⟦`/`⟧` (U+27E6/U+27E7) do not collide with the existing `[QUELLE N]`
  citation syntax and are not expected to occur in Moodle content.
- Name detection uses a local, offline spaCy German NER model (`de_core_news_md` by default);
  email detection uses a regex. Both run entirely on the machine — no additional network calls.
- Tokenization must happen before `content_sha256()` is computed for embedding/OCR cache keys,
  so those caches key off exactly what is actually sent to Gemini.
