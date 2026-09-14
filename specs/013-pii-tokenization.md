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
any text reaches an embedding call or an LLM prompt — while keeping the system able to answer a
question like "what's the teacher's email?" with the real value. The real value is substituted
back in locally, after the Gemini call returns, using a reversible mapping that never leaves the
machine. This is tokenization, not blind redaction.

**Threat model: the cloud provider, and only the cloud provider.** The local SQLite index is
inside the trust boundary. It has to be — `pii_tokens` stores every entity's plaintext
`original` in that same file, so tokenizing the rest of it protects nothing while the decoder
ring sits next to it. Anyone who can read `index.db` can already resolve every token. The
boundary that means something is egress, so that is where the guarantee is enforced (AC-25
onward), and the index stores raw text so retrieval can do its job (AC-13).

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
  page text and uploaded-file text. The course/section/module breadcrumb is a separate case, see
  AC-40: it is deliberately never run through NER at all, not tokenized-then-checked.
- `AC-13` Chunk text written to the store (`chunks.text`, `chunks.header_text`, `chunks_fts`,
  `meta["body"]`) is **raw**, and each has a tokenized twin — `chunks.text_tokenized`,
  `chunks.header_text_tokenized`, `meta["body_tokenized"]` — which is what may reach Gemini.
  Storing raw is what lets FTS5 do its job on names: its `remove_diacritics 2` folding and the
  `*` prefix matching in `fts5_escape` operate on real words, so `Muller` finds `Müller` and
  BM25 term overlap still links one surname across different documents. `documents.text` stays
  raw as before. `header_text_tokenized` is the identity of `header_text` — see AC-40, the
  breadcrumb it is built from is never run through NER, so there is nothing to transform.
- `AC-14` A live Matrix moderator message indexed via `index_matrix_message` is stored raw with
  a tokenized twin, the same as Moodle content, and its inline embedding call is given the
  tokenized form. The Matrix sender ID itself is never tokenized — it is a protocol identifier,
  not free-text content.

### Query-time protection and answer detokenization
- `AC-15` A student's question is tokenized before it is used for query condensing,
  decomposition, expansion, step-back, embedding, reranking, relevance evaluation, or the final
  answer-generation prompt — every one of these sends text to Gemini.
- `AC-16` Retrieved-chunk metadata surfaced to the LLM prompt and to citations —
  `course_name`, `module_name`, `title` — is read as-is from the raw `documents` table at query
  time. See AC-40: these are deliberately excluded from NER entirely, so there is no tokenized
  form to read instead.
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
  fully tokenized index. Originally true simply because changed content hashes made the existing
  "skip unchanged content" logic behave correctly on their own — before the storage flip (AC-13),
  `chunk.body` *was* the tokenized form, so any tokenization change was a body content change by
  construction. AC-13 broke that coincidence (`chunk.body` is now raw and invariant to detection
  logic), so the guarantee is now carried explicitly by AC-42 instead of following for free.

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
- `AC-37` Retrieval sends each half of the hybrid the form it needs: the tokenized query is
  embedded (the vectors it is compared against were built from tokenized chunk text, so both
  sides must agree), while BM25 — which runs entirely locally against the raw `chunks_fts` — is
  given the detokenized query. The name in that lexical query is restored from the token map
  rather than reproduced by the model, so a rewrite that mangles every surrounding word still
  searches for a correctly spelled name.
- `AC-38` `tokenize()` runs the same gazetteer as a second pass after NER, so a name already
  known from anywhere in the corpus is caught even where the model misses it. The German NER
  model is trained on capitalized prose — which Moodle documents are — while a student's
  question is lowercase and terse (`wer ist max müller`), so detection is systematically weaker
  on exactly the side that carries a live leak. Detection therefore improves as the corpus is
  indexed; the token itself stays a pure function of `(entity_type, normalized_text)`, so AC-2
  is unaffected, as is idempotency (AC-6).

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
- `AC-39` Independent of alias corruption (AC-32), a query-rewriting stage can also return a
  *well-formed* variant that simply never mentions the entity — the model writes around a
  placeholder it cannot interpret rather than reproducing it. `_keeping_pii_entities` drops such
  a variant by comparing, after aliasing has already restored real tokens, whether every entity
  present in the source question is still present in the rewrite. This is a second, independent
  filter from AC-32's alias-corruption check: the alias layer can only catch a *malformed* alias
  reference, not a rewrite that is syntactically clean but has silently changed subject. The two
  prompt templates' instruction to preserve a placeholder (`_PRESERVE_PII_INSTRUCTION`)
  describes the alias shape the model actually sees (`⟦PERSON_A⟧`), not the underlying hash
  token — a rewrite is reinforcement on top of both code-level filters, not the only thing
  standing between a dropped placeholder and a bad query.

### Detection precision

Recall (AC-38) improves what NER catches; nothing before this addressed the other direction —
what it wrongly catches. Auditing a live deployment found `de_core_news_md` misclassifying
ordinary German words, technical jargon, imperative verbs, filenames, URLs, and course codes as
`PERSON`, badly enough that citations and prompts were visibly corrupted and plausibly
contributing to inconsistent refusals: a source list where every label reads `⟦PIIPERSON…⟧`
instead of a real course name is the kind of thing rule 5 of the system prompt ("say so openly
if sources are unclear") can easily latch onto.

- `AC-40` Structured Moodle metadata — `course_name`, `section_name`, `module_name`, `title`,
  and the breadcrumb built from them (`header_path`/`header_text`) — is never run through NER,
  at ingest or at query time. These fields are assigned by the Moodle API
  (`course.get("fullname")`, `section.get("name")`, `module.get("name")`), not free text a
  student or teacher wrote, and auditing the live corpus found zero genuine person names among
  every metadata value that had ever been misdetected as `PERSON` — all 69 matches across all
  four fields (out of 2,024 distinct `PERSON` detections total) were course/class codes,
  filenames, or ordinary words. Running NER on them was pure precision cost for no measured
  protection. This is a deliberate scope decision, not a proof: a future document whose title
  genuinely names a real person (e.g. a resource a teacher named after themselves) would reach
  Gemini untokenized. Supersedes `PiiTokenizer.tokenize_path`, which mitigated the same failure
  class by giving NER more context rather than removing metadata from NER's input; the audit
  above showed that mitigation wasn't sufficient on its own (`Klassenkurs IT4bili` still
  misfired with all three breadcrumb segments present).
- `AC-41` A candidate `PERSON` span containing a digit is never accepted, at either the NER
  detection point or the gazetteer (AC-38). No real name contains a digit, so this costs no
  recall. It is the single largest remaining false-positive class after AC-40: course/class
  codes (`IT4L`, `DSSW10IE11-G`), filenames, URLs, and — the case worth calling out
  specifically — literal dates (`07.11.2023`), which would otherwise corrupt the date strings
  `HybridSearcher._date_search`/`_boost` depend on matching exactly.

### Reindexing when only detection logic changes

Found live, right after AC-40/AC-41 shipped: running `bsbot index --reset-all` did not fix a
single already-indexed chunk. `--reset-all` only clears `extract_version`, putting a document
back into `documents_needing_extraction()`'s queue — the actual "has anything worth rewriting
changed" decision is a separate check inside `_index_one`, comparing a hash of `chunk.body`
(plus `_augmentation_signature()`) against the document's stored `text_sha256`. Since AC-13's
storage flip, `chunk.body` is raw and untouched by anything `PiiTokenizer` does — AC-40 and
AC-41 change only the derived `text_tokenized`/`header_text_tokenized` columns, so the hash
matched, every document was skipped, and 65% of the live corpus's chunks kept stale tokenized
headers through a `--reset-all` that appeared to run cleanly. This is the identical failure
mode `_augmentation_signature()` already exists to prevent for `--hype`/`--summarize`
/`--contextualize` — the same class of bug, just not extended to cover the PII tokenizer.

- `AC-42` A PII-detection logic version is folded into the same change-detection hash, active
  whenever a tokenizer is configured. Bumping it is what makes a `--reset-all` actually rewrite
  a chunk whose raw body is unchanged but whose desired tokenized view now differs — this is
  what AC-24 now depends on explicitly, rather than getting it by accident the way the
  pre-storage-flip architecture did. Whoever changes what `PiiTokenizer.tokenize()` detects —
  a new normalization rule, a new exclusion, a NER model swap — is responsible for bumping this
  alongside the change, the same manual discipline `EXTRACT_VERSION` already requires for
  extraction-logic changes.

## Non-goals

- Phone numbers, postal addresses, and other identifiers are out of scope for this iteration.
- Image/OCR content is not protected — `transcribe_image`/`describe_image` send raw image bytes
  to Gemini directly; a name in a scanned document or photo is not caught by this text-level
  system.
- Coreference resolution across different surface forms of the same person (see AC-5).
- spaCy NER recall is not guaranteed — this is a probabilistic filter, not an absolute guarantee.
- Precision on free-text body content is not fully solved. AC-40/AC-41 remove metadata and
  digit-bearing spans, the two highest-volume false-positive classes found live, but an ordinary
  single word in running prose that happens to look like a name (a verb capitalized only because
  it starts a sentence, a compound noun the model hasn't seen) is not caught by either — this
  needs its own investigation (a POS-tag check against the parse spaCy already computes is the
  leading candidate, not yet implemented).

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
- A deployment that already ran the AC-24 rollout reindex needs to run
  `bsbot index --reset-all` again after AC-40/AC-41/AC-42 ship: existing
  `chunks.header_text_tokenized` rows and any document summary generated before this change can
  still hold hash tokens for metadata/digit-bearing spans that will no longer be produced going
  forward, and only a fresh extraction pass rewrites them. This note was wrong on its own before
  AC-42 landed — see AC-42's finding for why "run `--reset-all` again" alone did not work.
