# 003 — Course crawl and content model

- **Status:** active
- **Tests:** `tests/unit/test_crawler.py`, `tests/unit/test_content_model.py`

## Goal

Walk every enrolled course and produce a flat list of **content items** — the atomic,
text-bearing units the index is built from — together with enough provenance to cite each
one back to Moodle. This is pure structure discovery: nothing is downloaded here (spec 004)
and nothing is embedded (spec 006).

## Observed reality

Measured against `moodle.itech-bs14.de` on 2026-08-21 (Moodle 5.0.3+, 19 courses, 134
sections, 515 files, 697 MB). The design follows from these numbers:

| Module | Count | Where the text is |
|---|---|---|
| `label` | 144 | `description` HTML, inline. 64k chars total, up to 9k in one label — **real content, not decoration** |
| `resource` | 123 | attached files (mostly PDF) |
| `page` | 112 | `contents[]` → `index.html`, must be fetched |
| `url` | 98 | `contents[0].fileurl` → external link |
| `book` | 8 | one `index.html` per chapter, plus a `structure` item listing chapter titles |
| `folder` | 12 | multiple files under one module |
| `subsection` | 7 | Moodle 4.5+ container; holds no content itself |

## Acceptance criteria

### Discovery
- `AC-1` Every course from `core_enrol_get_users_courses` is walked with
  `core_course_get_contents`; one failing course is logged and skipped without aborting the rest.
- `AC-2` Each content item carries a **stable** `doc_id` derived from course id, course-module
  id and an ordinal, so re-crawling updates rows instead of duplicating them.
- `AC-3` Each item carries a `header_path` (`course › section › module › chapter`) used both as
  embedding context and as the human-readable citation label.
- `AC-4` Each item carries the Moodle view URL of its module, so an answer can link back to it.

### Text sources
- `AC-5` A `label`'s `description` becomes an inline text item; HTML is stripped to text.
- `AC-6` Any module carrying a non-empty `description` yields an inline item for that intro text,
  in addition to its file items.
- `AC-7` A `page` yields one item referencing its `index.html`, marked as HTML needing fetching.
- `AC-8` A `book` yields one item **per chapter**, titled from the `structure` content entry, so a
  citation names the chapter rather than the whole book.
- `AC-9` A `folder` yields one item per contained file.
- `AC-10` A `url` module yields an external-link item carrying the target URL, not a file.
- `AC-11` `subsection` modules produce no items of their own and do not break the walk.

### Filtering
- `AC-12` Items whose module is `visible: 0` or `uservisible: false` are skipped — the bot must
  never surface content the student cannot see.
- `AC-13` Binary-only file types (images, video, archives) are marked non-extractable and excluded
  from the text corpus, while remaining recorded for completeness.
- `AC-14` A file-size cap excludes the handful of very large files (3 files account for 123 MB of
  the 697 MB). **The cap must not apply to items Moodle reports as `filesize: 0`**, because
  generated `page`/`book` HTML is always reported as zero bytes and is the single largest source
  of text in the corpus.
- `AC-15` Assignment submissions, grades and gradebook data are never crawled.

### Change detection
- `AC-16` Each item records `timemodified`, preferring the module's `contentsinfo.lastmodified`
  when present, so a later sync can detect changes without downloading anything.
- `AC-17` The crawl result is comparable against a previous crawl, yielding added / changed /
  removed `doc_id`s, so removed content can be tombstoned.

## Non-goals

- No downloading, extraction, chunking or embedding.
- Forum posts are handled separately (spec 005); they need their own pagination.

## Notes

`filesize: 0` on `page`/`book` HTML (`AC-14`) is a real trap found while surveying the live site:
110 pages and 45 book chapters would have been silently dropped by a naive size filter, losing
most of the site's prose.
