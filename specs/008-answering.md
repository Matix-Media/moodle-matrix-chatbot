# 008 — Grounded answering

- **Status:** active
- **Tests:** `tests/unit/test_rag.py`

## Goal

Turn a classmate's question into a short, correct, **cited** German answer — or an honest
"das steht nicht in Moodle". A confidently wrong answer about an exam date is worse than no
answer at all, so refusal is a first-class outcome, not an error path.

## Acceptance criteria

### Pipeline
- `AC-1` Question → optional expansion → hybrid retrieval → optional rerank → answer.
- `AC-2` **Query expansion** rewrites the question into German variants plus the likely formal
  term, retrieving for each and fusing. This is what bridges "wann is die prüfung" to a
  document headed *Termin der Abschlussprüfung Teil 1*.
- `AC-3` Expansion failure (quota, timeout) degrades to the original question; it never fails
  the answer.
- `AC-4` Reranking reorders candidates by relevance before they reach the answer prompt, and
  its failure likewise degrades to fusion order.
- `AC-5` The number of chunks in the final context is bounded.

### Grounding
- `AC-6` The prompt contains only retrieved chunks, each labelled with an index the model is
  told to cite.
- `AC-7` With no retrieval results, no answer call is made at all and the refusal is returned.
- `AC-8` Answers carry citations resolved back to course, module URL and page.
- `AC-9` Citations the model invents (an index that was never offered) are dropped rather than
  rendered as a broken link.
- `AC-10` A refusal is detected and marked `grounded=False`, so callers can render it
  differently.

### Language and tone
- `AC-11` The system prompt is German and instructs the model to answer in the question's
  language.
- `AC-12` The model is explicitly forbidden from using knowledge outside the provided context.

### Safety
- `AC-13` Retrieved course text is inserted as **data**, never as instructions. A document
  containing "Ignoriere alle vorherigen Anweisungen" must not steer the model.
- `AC-14` An LLM failure surfaces as a friendly message, not a traceback.

### Corrective and date-aware retrieval

Benchmarked against a real corpus (`specs/012-benchmarks.md`): plain reranking measurably
*regressed* date-relative questions (a Blockplan chunk for "today" reordered behind a
topically-similar chunk for a different week) — the reranker sees bare excerpts with no way
to know which near-duplicate candidate carries the date `_boost` already promoted for.

- `AC-15` **Dated reranking** (`dated_rerank`, only takes effect together with `rerank`) gives
  the reranker today's date and each candidate's own "Stand" date, so it can prefer the
  chunk whose date actually matches instead of reordering by topical similarity alone.
- `AC-16` **Corrective retrieval filtering** (`crag_filter`) scores every candidate's relevance
  to the question and drops the ones below threshold before reranking/context — distinct from
  the pre-existing `crag` flag, which only swaps the refusal *text* (an actionable Moodle
  search link) and never touches which candidates reach the answer prompt.
- `AC-17` A `crag_filter` pass that scores every candidate below threshold degrades to the
  unfiltered candidate list rather than an empty one — an empty *context* would be
  indistinguishable from AC-7's "nothing retrieved at all" case, which skips the LLM call
  outright, for the wrong reason (a real corrective filter should still let the answer prompt
  see the (weak) evidence and refuse from it, not from having nothing to look at).

## Non-goals

- No conversational memory in this spec; each question is answered independently.
