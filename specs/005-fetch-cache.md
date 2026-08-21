# 005 — On-demand fetching and cache freshness

- **Status:** active
- **Tests:** `tests/unit/test_fetcher.py`, `tests/unit/test_nextcloud.py`

## Goal

Fetch document bytes **on demand**, cache them content-addressed, and keep the cache from
going stale without re-downloading 544 MB on every sync. The requirement is explicit: cached
files must expire or be revalidated so the bot never answers from outdated material.

## The freshness ladder

Cheapest check first; each rung only runs if the one above is inconclusive.

1. **No cache entry** → download.
2. **Moodle's `timemodified` changed** (already known from the free structure crawl) → download.
   This catches almost every real change at zero network cost.
3. **Within the soft TTL** (default 24 h) → serve from cache, no network at all.
4. **TTL elapsed** → conditional `GET` with `If-None-Match` / `If-Modified-Since`.
   A `304` costs a few hundred bytes and only refreshes `checked_at`.
5. **Bytes actually differ** (new sha256) → store the new blob and mark the document for
   re-extraction. Identical bytes → keep the existing blob and do *not* re-extract.

## Acceptance criteria

- `AC-1` A Moodle `pluginfile.php` URL is authenticated by appending the web service token;
  the token is never logged.
- `AC-2` A first fetch downloads, stores a content-addressed blob, and records
  `sha256`/`etag`/`last_modified`/`moodle_timemodified`/`fetched_at`/`checked_at`.
- `AC-3` A second fetch inside the TTL performs **no HTTP request**.
- `AC-4` A changed Moodle `timemodified` forces a download even inside the TTL.
- `AC-5` After the TTL, a conditional request is sent carrying both validators.
- `AC-6` A `304` response updates `checked_at`, keeps the cached bytes, and does not
  re-extract.
- `AC-7` A `200` with identical bytes reuses the existing blob and reports "unchanged".
- `AC-8` A `200` with different bytes stores a new blob and reports "changed".
- `AC-9` HTTP failures are recorded and not retried again within a cooldown, so one dead link
  does not slow every sync.
- `AC-10` Downloads exceeding the size cap are abandoned **while streaming**, without buffering
  the whole body into memory.
- `AC-11` Fetch concurrency is bounded.
- `AC-12` A blob is written atomically: an interrupted download never leaves a truncated file
  that a later run would trust.

## Nextcloud

- `AC-13` A public share URL (`/s/<token>`, with or without `/index.php`) is turned into its
  direct-download form.
- `AC-14` Query parameters Moodle appends (`?dir=/&editing=false`) are stripped before
  building the download URL.
- `AC-15` A password-protected or expired share is recorded as a skip with a clear reason,
  never a crash.
- `AC-16` The real filename is taken from `Content-Disposition` when present.
- `AC-17` Non-Nextcloud external links (Google Docs, HackMD, YouTube — 97 on the live site)
  are recognised as unsupported and skipped without a request.

## Non-goals

- No text extraction (spec 006).
- No crawling of arbitrary websites: only Moodle files and Nextcloud shares are fetched.
