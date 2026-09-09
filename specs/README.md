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
| [009](009-matrix-bot.md) | Matrix bot | `test_matrix_bot.py`, `test_matrix_auth.py`, `test_matrix_runner.py` | active |
| [010](010-linked-course-discovery.md) | Linked-course discovery and self-enrolment | `test_enrolment.py`, `test_crawler.py` | active |
| [011](011-external-adapters.md) | External content adapters | `test_hackmd.py`, `test_google_docs.py`, `test_youtube.py`, `test_taskcards.py` | active |
| [012](012-benchmarks.md) | RAG method benchmarks | `test_bench.py` | active |
| [013](013-pii-tokenization.md) | PII tokenization | `test_pii.py` | draft |
| [014](014-web-chat.md) | Web chat API | `test_web_api.py` | active |
| [015](015-index-augmentation.md) | Index-time retrieval augmentation | `test_indexer.py`, `test_chunk.py` | active |
| [016](016-matrix-oauth-resilience.md) | Matrix OAuth device-grant login and process resilience | `test_matrix_oauth.py`, `test_matrix_runner.py`, `test_write_env.py` | active |
| [017](017-sync-cron.md) | Scheduled sync cycle | `test_sync_loop.py` | active |
| [018](018-corpus-export.md) | Corpus export | `test_export.py` (not yet written) | draft |
| [019](019-microservice-split.md) | api owns the database; matrix and cron are HTTP clients | `test_web_api_internal.py`, `test_matrix_api_client.py`, `test_cron_api_client.py` | active |
| [020](020-package-reorg.md) | Package layout follows service ownership (shared/api/cron/cli) | full suite, `test_cli.py`, `test_write_env.py` | active |
