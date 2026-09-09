# 020 — Package layout follows service ownership

- **Status:** active
- **Tests:** the full existing suite (no behaviour changed, only import paths — every test
  module's own imports are the regression check), plus `tests/unit/test_cli.py` and
  `tests/unit/test_write_env.py` specifically for the `cli` package split.

## Goal

Spec 019 made `api` the only process that opens the Store, with `matrix` and `cron` talking to
it over HTTP — a functional split. It deliberately left the file tree as it was, because mixing
a functional change with a mechanical one in the same commit makes either one harder to review
or revert on its own.

With the functional boundary settled, `bsbot.rag`, `bsbot.index`, `bsbot.llm`, and `bsbot.pii`
are now used exclusively by `api`; the fetch/extract/adapter code (crawler, fetcher, extract,
the five spec 011 adapters) exclusively by `cron`; and `bsbot.cli` had grown into a single
1000+ line module covering all three deployed processes plus every local dev tool. This spec
moves files to match the ownership spec 019 already established, and splits `cli.py` into a
package along the same lines, so the directory layout stops lying about which service a file
belongs to. No behaviour changes — every moved file kept its content as-is; only import
statements (and, for `cli.py`, the split into command-group modules) changed.

## Acceptance criteria

- `AC-1` `src/bsbot/shared/` holds `config.py`, `logging.py`, `model.py` (`ContentItem` et al.)
  and `chunk.py` (`Segment`, `Chunk`, `chunk()`) — the DTOs and cross-cutting utilities both
  `api` and `cron` import directly, per spec 019's HTTP contract (`Segment` crosses the wire in
  `POST /internal/documents/{doc_id}/segments`; `ContentItem` in `POST /internal/crawl-result`).
- `AC-2` `src/bsbot/api/` holds everything only `api` runs: the FastAPI app, routes, auth,
  rate limiting, schemas (formerly `bsbot.web`), plus `rag/`, `index/`, `llm/`, `pii/`, and
  `indexer.py` (formerly top-level/`bsbot.ingest`).
- `AC-3` `src/bsbot/cron/` holds everything only `cron` runs: `sync_loop.py`, `crawler.py`,
  `fetcher.py`, `extract.py`, `segments.py`, the five spec 011 adapters, `api_client.py`
  (`CronApiClient`), and `moodle/` (only `cron` talks to Moodle now).
- `AC-4` `src/bsbot/matrix/` is unchanged (`bot.py`, `runner.py`, `oauth.py`, `api_client.py`) —
  it already lived at the right location before this spec.
- `AC-5` `src/bsbot/cli.py` becomes a package: `cli/_common.py` (`fail`/`settings`/`write_env`,
  used by every command), `cli/matrix.py` (`serve`, `matrix-login`), `cli/cron.py` (`cron`),
  `cli/api.py` (`serve-api`), `cli/dev.py` (`doctor`, `whoami`, `sync`, `index`, `embed`,
  `search`, `ask`, `chat`, `bench`, `export` — the local dev/research tools spec 019 left on
  direct `Store`/Moodle access). `cli/__init__.py` builds the one `Typer` app and registers
  each command via `app.command()(fn)` — `bsbot = "bsbot.cli:app"` in `pyproject.toml` needed no
  change, since a package's `__init__.py` satisfies the same import.
- `AC-6` `bsbot.eval` and `bsbot.export` stay at the top level, unmoved — research/admin tools
  spanning `api`'s internals (`Store`, `AnswerPipeline`, `extract()`), not part of any deployed
  service's own package.
- `AC-7` Every test file's assertions are unchanged; only `import` statements were updated
  (mechanical `sed`-style path rewrite) to point at the new locations. `test_write_env.py` is
  the one exception with a real edit: `bsbot.cli._write_env` (a private module-level function)
  became `bsbot.cli._common.write_env` (a public one, since it is now genuinely shared across
  `cli/matrix.py` and `cli/cron.py` rather than used only within one file).
- `AC-8` `ruff check`, `ruff format --check`, `mypy`, and `pytest --cov=bsbot` are all clean in
  a fresh venv reproducing CI exactly, and `bsbot --help`/`bsbot doctor` behave identically to
  before this spec.

## Non-goals

- No file's internal logic changed — this is a pure move-and-relink. Any further split of a
  still-large file (`store.py` at ~960 lines, `pipeline.py` at ~800, `cli/dev.py` at ~640) is a
  separate, riskier refactor and out of scope here.
- Per-service Dockerfiles/dependency sets — still deferred, per spec 019's own non-goals; all
  four services still build from the same image.
- OCR-over-HTTP — still deferred, per spec 019.

## Notes

Import paths changed (old → new); nothing on the right imports anything different than what
was already there, only from a different module:

| Old | New |
|---|---|
| `bsbot.config` | `bsbot.shared.config` |
| `bsbot.logging` | `bsbot.shared.logging` |
| `bsbot.ingest.model` | `bsbot.shared.model` |
| `bsbot.ingest.chunk` | `bsbot.shared.chunk` |
| `bsbot.web.*` | `bsbot.api.*` |
| `bsbot.rag.*` | `bsbot.api.rag.*` |
| `bsbot.index.*` | `bsbot.api.index.*` |
| `bsbot.llm.*` | `bsbot.api.llm.*` |
| `bsbot.pii.*` | `bsbot.api.pii.*` |
| `bsbot.ingest.indexer` | `bsbot.api.indexer` |
| `bsbot.ingest.{crawler,fetcher,extract,segments,api_client,google_docs,hackmd,nextcloud,taskcards,youtube}` | `bsbot.cron.{same name}` |
| `bsbot.sync_loop` | `bsbot.cron.sync_loop` |
| `bsbot.moodle.*` | `bsbot.cron.moodle.*` |
| `bsbot.cli` (module) | `bsbot.cli` (package: `matrix.py`/`cron.py`/`api.py`/`dev.py`/`_common.py`) |

A few cross-package imports are intentional, not leftover coupling: `bsbot.cron.api_client`
(the HTTP client `cron` uses) imports `Document`/`FetchRecord` from `bsbot.api.index.store` and
`Segment`/`ContentItem` from `bsbot.shared.*` purely as typed return values — it never
constructs a `Store`, matching spec 019 AC-1. `bsbot.export` and `bsbot.eval` similarly import
across `api`/`cron` package boundaries because they are local dev/research tools, not one of
the three deployed services, and were never meant to respect that boundary (spec 019's
non-goals already say so).
