# 011 — External content adapters

- **Status:** active
- **Tests:** `tests/unit/test_hackmd.py`, `tests/unit/test_google_docs.py`, `tests/unit/test_youtube.py`,
  `tests/unit/test_taskcards.py`, `tests/unit/test_crawler.py` (Moodle module links)

## Goal

Of 117 external links on the live corpus, 107 produced no text. Every mechanism here was verified
against the live site or the real service before being written — none of this is guessed from
documentation.

## HackMD

- `AC-1` A HackMD note URL (`hackmd.io/<id>`, `hackmd.io/@user/<id>`) resolves to its raw-markdown
  form by appending `.md`, confirmed against HackMD's own docs and consistent with the shapes
  found on the live corpus (both bare and `@user/`-prefixed forms exist).
- `AC-2` The fetched Markdown is extracted as plain text, reusing `extract_text`.

## Google Docs / Slides / Sheets

- `AC-3` A `docs.google.com/document/d/<id>/...` URL resolves to `.../export?format=txt`.
- `AC-4` A `docs.google.com/presentation/d/<id>/...` URL resolves to
  `.../export/pptx`, extracted with the existing `extract_pptx`.
- `AC-5` A `docs.google.com/spreadsheets/d/<id>/...` URL resolves to
  `.../export?format=xlsx`, extracted with the existing `extract_xlsx`.
- `AC-6` A document that is not shared publicly ("anyone with the link") fails the export fetch
  cleanly (Google returns an HTML sign-in page or 401/403) and is recorded as a normal fetch
  failure — never treated as if it succeeded with garbage content.

## YouTube

- `AC-7` A `youtube.com/watch?v=<id>` or `youtu.be/<id>` URL resolves to its video id.
- `AC-8` The transcript is fetched via `youtube-transcript-api`, preferring German, falling back
  to English, falling back to any available language — no API key needed, verified against
  public videos.
- `AC-9` A video with no captions available (disabled, or none auto-generated) is recorded as a
  clean failure, not an error that aborts the batch.

## TaskCards

Verified live via the real frontend (browser network trace against `itech-bs14.taskcards.app`,
2026-08-21): loading a board URL triggers `createVisitor` (an anonymous session — confirmed via a
direct unauthenticated request that this is *not* a credential bound to any account, board owner,
or the URL's own `?token=`) then a `board(id)` GraphQL query, which returns full content — for a
public board and for one flagged `private: true`. The adapter replicates exactly this sequence:
what already happens when anyone clicks the link, nothing more privileged.

- `AC-10` A TaskCards board URL (`<host>/#/board/<id>...`) resolves to its board id.
- `AC-11` `createVisitor` is called once to obtain a session token, then `board(id)` is queried
  with it as `x-token`.
- `AC-12` Board content (lists and cards: title + description) is rendered as structured text,
  grouped by list, so a card's context (which list/topic it belongs to) survives into the chunk.
- `AC-13` A board that returns a GraphQL error (deleted, genuinely inaccessible) is a clean
  failure, not a crash.

## Direct Moodle page/file links

Verified live: `core_course_get_course_module(cmid=N)` resolves a bare course-module id — the
`id=` in `mod/page/view.php?id=N` — to its `course`, `modname` and `instance`, without needing to
already know which course it belongs to.

- `AC-14` A same-host `mod/*/view.php?id=N` link resolves its course via
  `core_course_get_course_module`, and that course is crawled exactly like a linked course
  (spec 010) if not already known.
- `AC-15` A same-host `pluginfile.php/...` link is fetched directly with the existing
  authenticated fetcher — no resolution needed, it is already a file URL.
- `AC-16` A `core_course_get_course_module` failure (module deleted, genuinely inaccessible) is a
  clean skip, matching the existing linked-course failure handling.

## Non-goals

- No OAuth to Google Drive/Docs API — export URLs work only for publicly-shared documents, by
  design (AC-6 covers the failure case explicitly rather than working around it).
- No TaskCards attachment download (a card's own file attachments) — text content only for now.
