# 018 — Corpus export

- **Status:** draft
- **Tests:** `tests/unit/test_export.py` (does not exist yet — see Notes)

## Goal

`bsbot export` dumps the entire indexed corpus to plain files for uses bsbot itself doesn't
reach: pasting into another LLM's context window, manual review, offline search where a
citation link back to Moodle isn't available and full content has to travel in the file
itself. It writes one Markdown file per document, a master combined text/Markdown file across
every course, and that master file split along document boundaries into roughly 800,000-token
parts. It is a local, explicitly user-triggered action that never leaves the machine — unlike
embedding or answering, which always go to Gemini — which is what lets it show real names and
emails instead of PII tokens (spec 013).

## Acceptance criteria

### Per-document export
- `AC-1` Every non-tombstoned document in the store produces one Markdown file. Its filename is
  built from course, section and title plus the doc id, sanitized so it is always a valid
  filename (invalid characters replaced, whitespace collapsed, length capped), and
  de-duplicated with a numeric suffix if two documents would otherwise collide.
- `AC-2` A document's content is resolved in priority order: inline text already stored on the
  document → the on-disk blob re-extracted fresh (HTML converted to Markdown, other types via
  the same extractor spec 006 uses) → chunks already recorded for the document in the store →
  a fallback block (a cached AI image description/OCR transcript, else an external link, else a
  bare file-attachment reference, else an explicit "no text content" note).
- `AC-3` A standalone image blob (PNG/JPEG/WEBP) prefers its cached description/OCR text (spec
  006's vision cache) over rendering only a placeholder link, when that cache entry exists.
- `AC-4` Base64 data-URI images embedded in extracted HTML are replaced with a short bracketed
  placeholder rather than dumped as raw base64 — the export is meant to be read, not rendered.
- `AC-5` A re-extraction failure for one document's blob is caught and logged; that document
  falls through to its next content-resolution tier rather than aborting the whole export.
- `AC-6` Every document's file and both master files carry the same header block — course,
  section, module, type, source, Moodle URL, last-modified — so provenance travels with the
  content wherever it ends up, independent of bsbot's own citation mechanism.

### Master combined files
- `AC-7` A single combined `.txt`, and optionally a combined `.md`, file concatenates every
  document's block in course/section/module/title order, headed by a table of contents listing
  each course and its item count.
- `AC-8` The combined text is additionally split along document boundaries into successive
  parts, each capped at a configured token budget (`cl100k_base` tokenizer, default ~800,000) —
  a single document's block is never split across two parts.

### PII handling
- `AC-9` When PII tokenization (spec 013) is configured, tokenized content is detokenized back
  to real values before being written to any export file.
- `AC-10` Detokenization is applied uniformly across every content-resolution tier's output
  (AC-2), not only the one tier that happens to be tokenized today — a future tier that becomes
  tokenized must not silently leak raw tokens into an export whose entire point is human-readable
  real content.

## Non-goals

- No incremental export — every run regenerates every file from scratch.
- No export format beyond Markdown and plain text (no PDF, DOCX, or bundled HTML).
- No hyperlinking back into bsbot's own retrieval — a Moodle URL is included as plain text
  where available, nothing more.

## Notes

**No test module exists for this command today.** `src/bsbot/export.py` is 470+ lines of
non-trivial logic (`export_all`, `extract_document_markdown`, filename sanitization, image
placeholder cleanup, token-bounded splitting) with zero test coverage, unlike every other
command-sized feature in this repo. Per this repo's spec-first workflow (`specs/README.md`),
writing `tests/unit/test_export.py` against the acceptance criteria above — starting red — is
the next concrete step here, before any further changes to `export.py`. AC-9/AC-10 depend on
spec 013, itself still `draft`.
