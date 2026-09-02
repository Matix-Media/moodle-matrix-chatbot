# 012 — RAG method benchmarks

- **Status:** active
- **Tests:** `tests/unit/test_bench.py`

## Goal

Spec 007 and 008 accumulated several optional retrieval- and answer-time techniques —
query expansion, decomposition, step-back queries, reranking, the follow-up hop,
context compression, the CRAG actionable fallback — plus two index-time techniques,
HyPE document augmentation and semantic chunking (`specs/006`). Each was added on
plausible reasoning, but none has ever been measured against the others on the same
questions. This spec turns "which of these actually help, and by how much" from a
guess into a number: a small harness that runs a curated question set through the
pipeline once per technique (or combination) and reports comparable metrics.

## Acceptance criteria

### Golden question set
- `AC-1` A golden set loads from YAML: each entry has an `id` and a `question`; every
  other field is optional, so a set can mix precise entries (naming the exact expected
  document) with fuzzy ones (only naming expected keywords, or only an
  answerable/unanswerable expectation).
- `AC-2` A malformed entry (missing `id` or `question`, wrong field type) fails to load
  with an error naming the offending entry, not a stack trace from deep inside the
  runner.
- `AC-3` Duplicate `id`s across entries fail to load — a report keyed by id would
  otherwise silently merge two questions.

### Metrics
- `AC-4` **Retrieval-final**: whether an `expected_doc_id` (or, absent that, a document
  whose header matches an `expected_header_contains` substring) is present among the
  chunks that actually reached the answer prompt — the post-fuse/boost/diversify/rerank
  context, not just something some query variant happened to surface.
- `AC-5` **Retrieval-any**: the same check against the union of every hit returned by
  every query issued while answering the question (all expansions, sub-queries,
  step-back, and the follow-up hop). This isolates query-augmentation techniques from
  the fusion/rerank/diversify stage AC-4 measures.
- `AC-6` **MRR**: reciprocal rank of the first matching document within the final
  context order; `0.0` when no expected document is given, or none matched.
- `AC-7` **Answerable-correctness**: whether `Answer.grounded` matches the entry's
  `answerable` expectation (default `True`). A question marked `answerable: false`
  measures correct refusal, not retrieval quality — a hit found for it would be scored
  as the harness failing to test what it claims to test, not as a good result.
- `AC-8` **Keyword coverage**: the fraction of `expected_keywords` found as
  case-insensitive substrings of the final answer text; omitted (not zero) from the
  aggregate when an entry gives no keywords, so entries that only assert
  answerability don't drag down a coverage average they were never meant to feed.
  Also omitted (not scored against the refusal text) whenever the answer isn't
  grounded — found live: the CRAG actionable-fallback link embeds the original
  question verbatim as a Moodle search URL, so a keyword drawn from the question's
  own topic can appear in a *refusal* purely because the refusal echoes the
  question, not because anything was actually answered.
- `AC-9` **Cost proxy**: LLM call count and total prompt+response character count per
  question, recorded without depending on any provider-specific token accounting, so
  it works identically against `FakeLLM` in tests and a real client in production.
- `AC-10` An entry with no `expected_doc_ids` and no `expected_header_contains`
  contributes to answerable-correctness and keyword coverage only; it never silently
  counts as a retrieval miss.

### Comparing techniques
- `AC-11` A named preset maps to a set of `AnswerPipeline` keyword overrides (e.g.
  `baseline` — every optional technique off; `expand`, `rerank`, `decompose`,
  `step_back`, `compress`, `crag` — baseline plus exactly that one technique;
  `kitchen_sink` — everything on). Running the same golden set through every preset
  with the same searcher and LLM produces one comparison report.
- `AC-12` Running two presets that both retrieve (the common case) reuses the same
  `SearcherLike` and `LLMLike` instances — the harness does not require, and must not
  silently trigger, a re-index or re-embed between presets.
- `AC-13` HyPE and semantic chunking are index-time, not pipeline-time: they are
  compared by pointing the harness at two differently-built `index.db` files with the
  same preset, not by a runner-internal toggle. The harness itself is agnostic to what
  produced the searcher's hits, so this needs no special-case code — only two
  separately-built indexes and two runs to diff.
- `AC-14` A report serializes to JSON (one object per preset, with per-question detail
  and the aggregate), so results from two runs — before/after a pipeline change, or
  two index builds — can be diffed without rerunning either.

### Degradation
- `AC-15` A question that raises during answering (LLM failure, searcher failure) is
  recorded as a failed run for that question/preset pair with the error message, and
  does not abort the rest of the benchmark.

### Reproducibility
`AnswerPipeline` resolves relative-date questions ("heute", "am Montag") against a
`clock` that defaults to the real wall clock — so a golden entry whose expectation
depends on *which* date "today" resolves to is only reproducible if that clock is
pinned, otherwise the same entry can silently pass or fail depending only on which day
the benchmark happens to run.

- `AC-16` `bsbot bench --as-of YYYY-MM-DD` pins the pipeline's clock for the whole run
  (noon Europe/Berlin on that date), so a date-relative golden entry resolves the same
  "today" on every run rather than the day it happens to be invoked. Without `--as-of`,
  the harness still runs (defaults to the real clock, same as any other command) — this
  is an opt-in reproducibility aid, not a requirement for every golden set.

## Non-goals

- No automated corpus-specific golden set is shipped — the example file in
  `config/golden_questions.example.yaml` is illustrative only. A real golden set
  has to be curated from the deployment's own Moodle content, because the questions
  and expected documents are meaningless without it.
- No automatic multi-index orchestration (building a HyPE and a semantic-chunking
  index back-to-back). Index builds already exist (`bsbot index --hype`,
  `bsbot index --semantic`) and cost real Moodle/Gemini quota; the harness runs
  against whichever index it's pointed at, once, per invocation.
- The optional LLM-judge score (`--judge`) is a coarse 1-5 relevance rating, not a
  substitute for keyword coverage or a human review — it exists to catch the case a
  correct answer phrases differently than any keyword anticipated, not to replace the
  other metrics.

## Notes

**58-question golden set (2026-09-02):** the original 8 entries (Lernfeld-10-only,
edge-case-heavy) were extended with 50 everyday questions spanning employment law,
exam/documentation rules, school admin, communication skills, contract law, ethical
hacking, agile retros, Lernfeld 6, the Taiwan workshop, and the staff roster — chosen
to give `answerable_accuracy` real statistical weight instead of n=8, where a single
question flipping moves the number by 12 points. On the full set, `baseline` scores
`answerable_accuracy=0.78`, `retrieval_hit_final=0.67` — much lower than the original
8-question set's near-perfect baseline, because the larger set includes realistic
lexical/topical overlap the small set never exercised (see next paragraph).
`expand_dated_rerank` improved both in every one of three independent runs (two on the
58-question set, one on the original 8) — `answerable_accuracy` 0.78 → 0.86-0.90,
`retrieval_hit_final` 0.67 → 0.79-0.83 — but **the specific questions that flip
between runs are not fully stable**: `AnswerPipeline.generate()` for the final answer
is not temperature-pinned, so two runs of the identical preset over the identical
golden set can disagree on a handful of borderline questions (observed:
`blockplan-heute`/`montag-schulbeginn`, fixed in one run, regressed in another). Only
retrieval-side metrics (`retrieval_hit_final`, `mrr`, computed from what actually
reached the prompt) are fully deterministic run to run; treat single-run
`answerable_accuracy`/`keyword_coverage` deltas as directionally reliable, not exact,
until the harness supports averaging multiple runs (not yet built).

**A genuine corpus characteristic, not a golden-set error:** several employment-law
questions (`kuendigung-schriftform` and siblings) retrieve worse under hybrid
(keyword+vector) search than keyword-only — a *different* course unit
("Mitbestimmung im Betrieb") covers overlapping Kündigungsschutz/Betriebsrat
territory, and vector similarity pulls its content in ahead of the more specifically
on-topic source. `bsbot search --keyword-only` puts the right document at rank 1;
the fused hybrid ranking does not. This is real, repeatable retrieval headroom this
benchmark surfaced that the original 8-question set (confined to one Lernfeld) could
not have found.

`_evaluate_relevance` in `pipeline.py` was dead code as of the first version of this
spec — nothing called it. It is now wired up as `crag_filter` (`specs/008-answering.md`
AC-16), a genuine corrective-retrieval filter distinct from `crag`'s refusal-text-only
effect; both remain separate presets since they measurably do different things.

A first real run against this deployment's corpus (`config/golden_questions.yaml`,
2026-09-01/02) found that plain `rerank` and `step_back` both regress date-relative
questions — a Blockplan chunk for "today" gets reranked behind a topically-similar
chunk for a different week, because the reranker sees bare excerpts with no way to
tell which one `_boost` already promoted for its date. `dated_rerank` (AC-15) and
`crag_filter` (AC-16) were added specifically in response to that finding — the
`dated_rerank` / `crag_filter` / `dated_rerank_crag_filter` / `best_guess` presets in
`bsbot.eval.runner.PRESETS` exist to check whether they actually fix it, not on
theoretical grounds alone. `bsbot index --contextualize` (`specs/006-extraction-chunking.md`
AC-25) is the index-time counterpart, comparable via the two-index procedure in AC-13.

Results from that same run (`bsbot bench --as-of 2026-09-01`, `dated_rerank` and
`crag_filter` isolated, then recombined):

- `dated_rerank` alone and `crag_filter` alone each independently fixed the
  regression — both reached the same `answerable_accuracy` as `expand` alone.
- Combining them (`dated_rerank_crag_filter`) *reintroduced* a miss, on a different
  question (`design-thinking-task`) — `crag_filter`'s per-candidate relevance scoring
  becomes less reliable over a larger, more topically-similar candidate pool. Adding
  `expand` on top (`best_guess`) made it worse still: `crag_filter` dropped the
  correct dated Blockplan chunk `expand`'s wider pool had actually found.
  `expand_dated_rerank` (same preset, `crag_filter` removed) scored a clean 1.00 on
  every accuracy metric — `crag_filter` is the specific technique that doesn't
  compose safely with query-widening ones here, not `dated_rerank`.
- `bsbot index --contextualize`, piloted on the golden set's own 5 documents (a
  `data_contextual` copy, not the production index): *regressed* `blockplan-heute` on
  both `baseline` and `expand_dated_rerank` — the correct dated chunk stopped
  reaching the final context at all (previously the pipeline's own `_boost` reliably
  promoted it). Likely cause: contextualizing every chunk of an internally-repetitive
  multi-week schedule document with a similarly-worded LLM-written situating sentence
  homogenizes their embeddings/BM25 profiles enough to blur exactly the
  week-to-week distinction `_boost`'s exact date-string match was already resolving
  precisely — the opposite of the intended effect, on this specific document shape.
  Not evidence it hurts everywhere: a Blockplan's repetitive per-week structure is an
  unusually adversarial case for it; a verbose prose document without an existing
  precise boost signal is the case it was chosen for. Untested here either way.
