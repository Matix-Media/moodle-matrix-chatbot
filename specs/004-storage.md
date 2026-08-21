# 004 — Storage: manifest, blob cache, chunks and vectors

- **Status:** active
- **Tests:** `tests/unit/test_store.py`

## Goal

One SQLite file holds everything: the crawl manifest, file-cache bookkeeping, chunk text,
the FTS5 keyword index and the `vec0` vector index. A single transactional store means the
index can never drift out of sync with the manifest, and backup is `cp index.db`.

## Acceptance criteria

### Schema and migration
- `AC-1` Opening a store creates the schema; opening it again is a no-op and preserves data.
- `AC-2` The schema version is recorded (`PRAGMA user_version`) and a newer-than-supported
  database is refused rather than silently corrupted.
- `AC-3` `sqlite-vec` and FTS5 are both available on the connection, verified at open time so
  failures surface at startup rather than mid-query.
- `AC-4` WAL mode and foreign keys are enabled.

### Manifest
- `AC-5` A crawl is persisted with one row per `ContentItem`, keyed by `doc_id`.
- `AC-6` Re-persisting the same crawl updates rows in place — no duplicates, and `first_seen`
  is preserved while `last_seen` advances.
- `AC-7` Documents absent from a later crawl are **tombstoned**, not deleted, and tombstoned
  documents are excluded from retrieval. Stale answers are worse than missing ones.
- `AC-8` A tombstoned `doc_id` that reappears in a later crawl is revived.
- `AC-9` The store reports which documents need (re-)extraction: never extracted, changed
  `timemodified`, or extracted under an older `extract_version`.

### Blob cache
- `AC-10` Blobs are content-addressed by sha256 and stored under `blobs/<aa>/<sha256>`, so two
  modules linking the same file share one copy on disk.
- `AC-11` Fetch bookkeeping is per URL: `etag`, `last_modified`, `moodle_timemodified`,
  `fetched_at`, `checked_at`, and the resulting `sha256`.
- `AC-12` A permanently failed fetch is recorded with its error so it is not retried every sync.
- `AC-13` Deleting a document does not orphan a blob still referenced by another document;
  unreferenced blobs are reported by a `prune` query rather than deleted implicitly.

### Chunks and vectors
- `AC-14` Chunks belong to a document and are replaced atomically when it is re-extracted:
  no window in which a document has both old and new chunks.
- `AC-15` Deleting or tombstoning a document removes its chunks from `chunks`, the FTS index
  and the vector index together.
- `AC-16` Embeddings are cached keyed by `sha256(text) + model + dim + task_type`, so
  re-chunking only pays for text that actually changed.
- `AC-17` Vectors are stored **normalised**, because `gemini-embedding-001` returns
  un-normalised vectors at reduced dimensionality (measured norm ≈ 0.58 at 768 dims) and
  cosine ranking over raw vectors would be subtly wrong.

## Non-goals

- No retrieval logic here (spec 007) — this is storage and bookkeeping only.
