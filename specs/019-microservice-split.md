# 019 — api owns the database; matrix and cron are HTTP clients

- **Status:** active
- **Tests:** `tests/unit/test_web_api_internal.py`, `tests/unit/test_matrix_api_client.py`,
  `tests/unit/test_cron_api_client.py`, `tests/unit/test_matrix_bot.py`, `tests/unit/test_indexer.py`,
  `tests/unit/test_fetcher.py`

## Goal

Before this spec, `matrix`, `cron`, and `api` each independently opened the same SQLite `Store`
file directly — three separate processes touching one file, relying entirely on WAL mode's
one-writer-many-readers guarantee to not corrupt anything. That is exactly the kind of shared-
mutable-state setup that already produced one real production incident (`/api/ask` crashing with
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same
thread`, fixed by making the route `async def` — see git history on `src/bsbot/web/app.py`), and
nothing stopped `matrix`'s moderator-message write and `cron`'s indexing write from racing on the
same file next.

The fix is ownership, not more defensive locking: `api` is now the only process that ever opens
the Store. `matrix` and `cron` talk to it over HTTP for everything they used to do locally.

## Acceptance criteria

### `api`
- `AC-1` `api` is the only process that constructs a `Store`. It exposes internal endpoints
  (bearer-token authenticated, same secret as spec 014's `/api/ask`) for every write `matrix`/
  `cron` used to make directly: `POST /internal/crawl-result` (persist a crawl, returns what's
  pending), `GET /internal/documents/pending`, `GET`/`POST /internal/fetch-record`, `POST
  /internal/fetch-record/touch`, `POST /internal/fetch-failures`, `GET`/`POST`/`HEAD
  /internal/blobs[/​{sha256}]`, `POST /internal/documents/{doc_id}/segments`, `POST
  /internal/embed-pending`, `POST /internal/ingest-message`.
- `AC-2` `POST /internal/documents/{doc_id}/segments` runs `Indexer.index_segments()` — PII-
  tokenization, chunking (including the spec 015 augmentations), and storage — against segments
  the caller resolved itself, not ones `api` fetches. A document with no segments (`segments:
  null`, a failed/skipped fetch) still runs this call so an alias-only chunk (spec 007 AC-19/20)
  still gets indexed even when the underlying content couldn't be fetched or extracted.
- `AC-3` `POST /internal/embed-pending` embeds every chunk lacking a vector using `api`'s own
  already-built `Store`/`GeminiEmbedder` — no chunk text crosses the wire to reach it.
- `AC-4` `POST /internal/ingest-message` runs `Store.index_matrix_message()` — the moderator-
  message-embedding feature `matrix` used to call directly.

### `matrix`
- `AC-5` `BerufsschuleBot` holds no reference to `Store`, `GeminiEmbedder`, or a PII tokenizer —
  only a `PipelineLike` (existing, spec 009) and a new `IngestMessageLike`, both satisfied by
  `bsbot.matrix.api_client.ApiClient` in production, which calls `api` over HTTP for both.
- `AC-6` `ApiClient`'s calls block synchronously rather than truly overlapping matrix-nio's event
  loop — an accepted tradeoff, the same one already made for `pipeline.answer()` blocking before
  this existed.

### `cron`
- `AC-7` `Fetcher` depends on a `FetchCache` protocol, not a concrete `Store` — the same class
  runs unmodified whether backed by a local `Store` (the `bsbot index` dev tool) or
  `bsbot.ingest.api_client.CronApiClient` (the deployed `cron`/`sync` containers).
- `AC-8` `FetchResult` carries the fetched bytes directly (`data`) instead of the caller reading
  them back from a blob cache it might not have — the one change needed inside `Fetcher` itself
  to make `AC-7` possible.
- `AC-9` `resolve_segments()` (fetch + extract: `CourseCrawler`, the on-demand `Fetcher`, `extract()`,
  and all five spec 011 external adapters) runs entirely without a `Store`. Chunking, PII, HyPE/
  summarize/semantic (spec 015), embedding, and storage do not — they run in `api`, reached via
  `index_segments()`/`embed_pending()`.
- `AC-10` Every document `api` reports pending is submitted to `/internal/documents/{doc_id}/segments`
  regardless of whether fetch/extract succeeded locally (`segments: null` otherwise) — matching
  `AC-2`'s alias-chunk requirement, and matching this spec's own step-isolation counting: a local
  fetch/extract failure is still counted in `cron.index`'s summary log even though the submitted-
  with-`null`-segments call itself touches none of `api`'s own counters.
- `AC-11` `sync_loop.py`'s `cron`/Web-API config replaces its previous Gemini config and aliases-
  file dependency — neither is needed locally anymore, both live entirely in `api`.

## Non-goals

- OCR-over-HTTP: a scanned page needing vision-fallback OCR (spec 006) gets whatever `extract()`
  returns without it under the new `cron` path — the same result as OCR being unconfigured. `api`
  is the only process with Gemini vision access now; wiring an HTTP OCR callback into `cron`'s
  extraction step is deliberately deferred, not attempted here.
- Per-service Dockerfiles/dependency sets: all four services still build from the same image.
  `cron`'s and `matrix`'s real dependency footprint shrank a lot (no more spaCy, sqlite-vec,
  matrix-nio-only vs. httpx-only) but splitting the build is a separate, lower-risk follow-up.
- The package/directory reorg this split makes low-risk (`bsbot.rag`/`index`/`llm`/`pii` are now
  used exclusively by `api`; fetch/extract code exclusively by `cron`) is not done here — this
  spec covers only the functional ownership change, not moving files to match it.
- `bsbot export`/`bsbot bench` and the standalone `bsbot ask`/`chat`/`sync`/`index`/`embed` CLI
  commands are unaffected — local dev/research tools, not part of the deployed topology, still
  using direct `Store` access exactly as before.

## Notes

`docker-compose.yml`: `matrix` and `cron`/`sync` no longer mount the shared `bsbot-data` volume
at all — only `api` does. `matrix` gets its own new volume (`matrix-session`) for just the E2EE
session store, which was always structurally separate from the RAG index (`config.py`'s
`matrix_store_dir` vs. `index_db`, both under the same `data_dir` before this, coincidentally).
`cron`/`sync` need no persistent volume at all now.

Verified live against real data during implementation, not just unit tests: a full
crawl → fetch/extract → index → embed cycle run via `docker compose run --rm sync` against a real
Moodle site (25 courses, 1033 items, 1072 chunks) completed correctly through the new HTTP path,
and a second consecutive cycle correctly no-op'd (idempotent skip) on unchanged content.
