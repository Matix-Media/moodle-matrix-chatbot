# 006 — Text extraction and chunking

- **Status:** active
- **Tests:** `tests/unit/test_extract.py`, `tests/unit/test_chunk.py`, `tests/unit/test_gemini_client.py`

## Goal

Turn cached bytes into clean text with **page/slide provenance**, then split it into retrieval
units that carry their own context. Corpus composition on the live site: 201 PDFs, 155 generated
HTML docs (page + book chapters), 26 DOCX, 12 PPTX, 9 XLSX, 6 DOC, plus 157 inline label texts.

## Acceptance criteria

### Extraction
- `AC-1` Every extractor returns `Segment`s carrying `text` and an optional `page` label, so an
  answer can cite "Skript.pdf, S. 4" rather than just the file.
- `AC-2` PDF text is extracted per page, preserving reading order.
- `AC-3` PPTX yields one segment per slide, numbered; speaker notes are included.
- `AC-4` DOCX includes table cell text, not just paragraphs — timetables and grading schemes live
  in tables.
- `AC-5` XLSX yields one segment per sheet, with the sheet name as its label.
- `AC-6` HTML is reduced to readable text with `<script>`/`<style>` and navigation removed.
- `AC-7` An extractor is chosen by extension *and* magic bytes, so a mislabelled file does not
  crash the run. Real files exist with no extension at all (`index.html` for pages).
- `AC-8` An unsupported or corrupt file yields a recorded failure, never an exception that
  aborts the batch.
- `AC-9` Extraction is deterministic: the same bytes give byte-identical text, so the content
  hash is a reliable change signal.

### Scanned documents
- `AC-10` A PDF page yielding less than a threshold of characters is flagged as needing OCR.
- `AC-11` OCR runs through an injectable interface, so the pipeline is testable without calling
  a paid API, and results are cached per page image hash — a scan is paid for once.
- `AC-12` If OCR is unavailable, the page is skipped with a reason rather than failing the file.

### Chunking
- `AC-13` Chunks target a configured size with overlap, measured in characters.
- `AC-14` Splits prefer paragraph, then sentence, then word boundaries — never mid-word.
- `AC-15` Every chunk carries its `header_path` breadcrumb as a prefix, which both improves
  embedding quality and gives the model what it needs to cite.
- `AC-16` A chunk records the page/slide it came from; a chunk spanning pages records the first.
- `AC-17` Short documents produce exactly one chunk, with no padding.
- `AC-18` Whitespace is normalised and repeated blank lines collapsed, so identical content
  chunks identically regardless of source formatting.
- `AC-19` A single unsplittable run longer than the target (a giant table row) is emitted whole
  rather than being dropped or cut mid-token.

## Non-goals

- No embedding (spec 007). No OCR *implementation* here beyond the interface and cache.
- No legacy `.doc`/`.ppt` (binary OLE2) support. The only reliable extractor for these is a
  LibreOffice headless conversion subprocess, which was weighed against the Docker image
  cost (hundreds of MB) for 7 files (2%) of the live corpus and explicitly declined.

## Coverage extensions (2026-08-21)

Verified against the live corpus (333 file-kind documents): 16 files had no extractor at all,
60 standalone image attachments were excluded before extraction was even considered.

- `AC-20` `.sql` files are extracted as plain text.
- `AC-21` `.xls` (legacy binary Excel) is extracted via `xlrd`, one segment per sheet — the
  same shape as `.xlsx`, without pulling in `pandas` for a single file.
- `AC-22` Standalone image attachments are no longer excluded before extraction; a page
  yielding an `ocr` callable transcribes them exactly like a scanned PDF page. Without an
  `ocr` callable, they are processed to zero chunks (matching the existing scanned-PDF
  behaviour), revisitable later via `--ocr --retry-empty`.
- `AC-23` Image magic bytes are checked before the extension, matching the existing
  PDF/OOXML dispatch pattern — a mislabelled image is still recognised.
- `AC-24` Coverage is PNG, JPEG and WEBP — exactly what Gemini's image-understanding API
  accepts (verified against Google's docs), not the broader "any image format" set. SVG is
  excluded on purpose: it's vector/XML markup, not a raster image, and needs a different
  handling path. The OCR call declares the *actual* detected format to Gemini rather than
  assuming PNG — a real gap caught during implementation: the original OCR interface hardcoded
  `mime_type="image/png"`, harmless while the only caller was the always-PNG PDF-scan path,
  but would have silently mis-declared WEBP bytes the moment a second image format was added.
- `AC-27` Beyond verbatim transcription, a cached image or diagram can instead be run through a
  semantic **description** pass (`CachingOcr(mode="describe")` / `GeminiClient.describe_image`)
  — a short structured explanation of what the image depicts (an architecture, a flow, a table)
  rather than a literal transcript, for images whose value to a searcher is diagrammatic rather
  than textual. It is cached separately from the verbatim-transcription cache, under its own
  tag, keyed on image bytes only — the same bytes never pay for either pass twice.
- `AC-28` Automatic function calling — which none of bsbot's Gemini calls ever need, since
  `tools=` is never passed — is explicitly disabled on every call (`generate`, `transcribe_image`,
  `describe_image` alike) rather than left at the SDK default, which otherwise prints a console
  advisory on every single request and adds pointless bookkeeping.

## Index-time augmentation (2026-09-02)

`bsbot index` grew several optional per-chunk/per-document augmentation passes over time
(`--hype`, `--summarize`, `--semantic`) without this spec being updated for them. Of those,
only the one below was documented here at the time; **`--hype`, `--summarize` and
`--semantic` are now specified in full in `specs/015-index-augmentation.md`**, which closes the
gap this addendum originally only flagged.

- `AC-25` **Contextual retrieval** (`--contextualize`, Anthropic's published technique):
  prepends a short LLM-written sentence to a chunk's own indexed text, situating it within its
  document (which week, which Lernfeld) before embedding — the same problem `AC-15`'s
  `header_path` prefix addresses at the document level, applied per-chunk. Targets the
  Blockplan-chunk case `DEFAULT_NEIGHBOR_RADIUS` in `rag/pipeline.py` already documents: a
  schedule document arbitrarily cut into fixed-size chunks, where a chunk's own text often
  cannot say by itself which week it covers. Skips a document's own `--summarize` summary
  chunk when both are enabled together — contextualizing an already-generated summary would
  be circular.
- `AC-26` Enabling `--hype`, `--summarize`, or `--contextualize` for a document already
  extracted without it reprocesses that document, even though its raw text hasn't changed.
  Found live running `AC-25`'s first real pilot: the unchanged-content skip in `_index_one`
  hashed only chunk *bodies*, which none of these three augmentations touch — they only
  extend the *indexed* text, built after that hash check — so flipping one of these flags on
  for already-extracted content spent the LLM call and then silently discarded the result at
  the skip. Fixed by folding which augmentations are active into the hash alongside the
  content itself, so identical content *and* identical augmentation flags still skip (the
  original point of the check), but a newly-enabled flag no longer does. This bug predates
  `--contextualize` — it affected `--hype`/`--summarize` identically, from whenever either
  was first turned on for an already-indexed corpus.
