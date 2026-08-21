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

## Non-goals

- No conversational memory in this spec; each question is answered independently.
