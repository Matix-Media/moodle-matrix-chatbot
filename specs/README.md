# Specs

Every feature starts as a numbered spec here, **before** any test or implementation is written.
Each spec maps 1:1 to a test module, named in its front-matter, so there is always a mechanical
answer to "where is this behaviour verified?".

## Workflow

1. Write `specs/NNN-name.md` using the template below.
2. Write the test module it names. All acceptance criteria start failing (red).
3. Implement until green. Refactor.
4. If behaviour changes later, the spec changes first.

## Template

```markdown
# NNN — Title

- **Status:** draft | active | superseded by NNN
- **Tests:** `tests/unit/test_x.py`

## Goal
One paragraph: what this enables and why it exists.

## Acceptance criteria
Numbered, each independently testable, each phrased as an observable behaviour.
`AC-1`, `AC-2`, ... — tests reference these IDs in their docstrings.

## Non-goals
What this deliberately does not do, so scope creep is visible.

## Notes
Design rationale, external constraints, links.
```

## Index

| Spec | Title | Tests | Status |
|------|-------|-------|--------|
| [001](001-configuration.md) | Configuration and secrets | `test_config.py` | active |
| [002](002-moodle-access.md) | Moodle auth and capability probe | `test_moodle_client.py`, `test_moodle_params.py` | active |
| [003](003-content-model.md) | Course crawl and content model | `test_crawler.py` | active |
| [004](004-storage.md) | Storage, cache, chunks and vectors | `test_store.py` | active |
| [005](005-fetch-cache.md) | On-demand fetching and freshness | `test_fetcher.py`, `test_nextcloud.py` | active |
| [006](006-extraction-chunking.md) | Extraction and chunking | `test_extract.py`, `test_chunk.py` | active |
| [007](007-retrieval.md) | Embeddings and hybrid retrieval | `test_embed.py`, `test_search.py` | active |
| [008](008-answering.md) | Grounded answering | `test_rag.py` | active |
| [009](009-matrix-bot.md) | Matrix bot | `test_matrix_bot.py` | active |
