"""SQLite store — see ``specs/004-storage.md``.

Everything lives in one file: manifest, blob bookkeeping, chunk text, the FTS5
keyword index and the ``vec0`` vector index. Keeping them in one transactional
store is what guarantees the indexes cannot drift away from the manifest — a
document's chunks, keywords and vectors are always written and removed together.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import time
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import sqlite_vec
import structlog
from pydantic import BaseModel

from bsbot.ingest.model import ContentItem, ContentKind, FileRef

log = structlog.get_logger(__name__)

SCHEMA_VERSION = 1
DEFAULT_EMBED_DIM = 768


class StoreError(RuntimeError):
    """Storage failure."""


class StoreVersionError(StoreError):
    """The database was written by a newer version of bsbot."""


class Document(BaseModel):
    """A manifest row."""

    doc_id: str
    course_id: int
    course_name: str
    section_name: str
    module_id: int
    module_name: str
    modname: str
    title: str
    kind: str
    header_path: list[str]
    module_url: str | None
    timemodified: int
    text: str | None
    file_url: str | None
    filename: str | None
    filesize: int
    mimetype: str | None
    external_url: str | None
    extractable: bool
    skip_reason: str | None
    text_sha256: str | None
    blob_sha256: str | None
    extract_version: int | None
    first_seen: int
    last_seen: int
    tombstoned_at: int | None

    @property
    def header_text(self) -> str:
        return " › ".join(self.header_path)


class FetchRecord(BaseModel):
    url: str
    sha256: str | None
    etag: str | None
    last_modified: str | None
    moodle_timemodified: int | None
    fetched_at: int | None
    checked_at: int | None
    error: str | None


class Chunk(BaseModel):
    chunk_id: int
    doc_id: str
    ordinal: int
    text: str
    header_text: str
    page: int | None
    meta: dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id           TEXT PRIMARY KEY,
    course_id        INTEGER NOT NULL,
    course_name      TEXT NOT NULL,
    section_name     TEXT NOT NULL DEFAULT '',
    module_id        INTEGER NOT NULL,
    module_name      TEXT NOT NULL DEFAULT '',
    modname          TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL,
    header_path      TEXT NOT NULL DEFAULT '[]',
    module_url       TEXT,
    timemodified     INTEGER NOT NULL DEFAULT 0,
    text             TEXT,
    file_url         TEXT,
    filename         TEXT,
    filesize         INTEGER NOT NULL DEFAULT 0,
    mimetype         TEXT,
    external_url     TEXT,
    extractable      INTEGER NOT NULL DEFAULT 1,
    skip_reason      TEXT,
    text_sha256      TEXT,
    blob_sha256      TEXT,
    extract_version  INTEGER,
    extracted_at     INTEGER,
    extracted_timemodified INTEGER,
    content_changed_at INTEGER,
    first_seen       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL,
    tombstoned_at    INTEGER
);
CREATE INDEX IF NOT EXISTS documents_course ON documents(course_id);
CREATE INDEX IF NOT EXISTS documents_live   ON documents(tombstoned_at);

CREATE TABLE IF NOT EXISTS blobs (
    sha256     TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fetches (
    url                 TEXT PRIMARY KEY,
    sha256              TEXT,
    etag                TEXT,
    last_modified       TEXT,
    moodle_timemodified INTEGER,
    fetched_at          INTEGER,
    checked_at          INTEGER,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    header_text TEXT NOT NULL DEFAULT '',
    page        INTEGER,
    text_sha256 TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

-- unicode61 + diacritic folding so 'Prufung' also matches 'Prüfung'.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    header_text,
    content='chunks',
    content_rowid='chunk_id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS ocr_cache (
    image_sha256 TEXT NOT NULL,
    model_tag    TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (image_sha256, model_tag)
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    text_sha256 TEXT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    task_type   TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (text_sha256, model, dim, task_type)
);
"""


class Store:
    def __init__(self, path: Path | str, *, embed_dim: int = DEFAULT_EMBED_DIM) -> None:
        self.path = Path(path)
        self.embed_dim = embed_dim
        self.blobs_dir = self.path.parent / "blobs"
        self._con: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row

        try:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError) as exc:  # pragma: no cover
            raise StoreError(
                "sqlite-vec could not be loaded. The Python build must support SQLite "
                "loadable extensions (the system Python on macOS often does not; the "
                "project venv and the Docker image both do)."
            ) from exc

        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            con.close()
            raise StoreVersionError(
                f"{self.path} has schema version {version}, but this bsbot supports "
                f"{SCHEMA_VERSION}. Upgrade bsbot rather than risk corrupting the index."
            )

        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(_SCHEMA)
        self._migrate(con)
        con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
            f"chunk_id INTEGER PRIMARY KEY, embedding float[{self.embed_dim}])"
        )
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
        self._con = con

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Additive, backward-compatible column adds for pre-existing databases.

        ``CREATE TABLE IF NOT EXISTS`` in ``_SCHEMA`` only creates a table the
        first time — it never alters one that already exists, so a column added
        to that string later needs an explicit ``ALTER TABLE`` here or every
        database created before that change breaks on the first query touching it.
        """
        columns = {row[1] for row in con.execute("PRAGMA table_info(documents)")}
        if "content_changed_at" not in columns:
            con.execute("ALTER TABLE documents ADD COLUMN content_changed_at INTEGER")

    def close(self) -> None:
        if self._con is not None:
            self._con.commit()
            self._con.close()
            self._con = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._con is None:
            raise StoreError("Store is not open")
        return self._con

    # ------------------------------------------------------------------ #
    # Manifest
    # ------------------------------------------------------------------ #

    def persist_crawl(self, items: Iterable[ContentItem], *, now: int | None = None) -> None:
        """Upsert a crawl and tombstone anything that has disappeared (AC-5..AC-8)."""
        stamp = now if now is not None else int(time.time())
        con = self.connection
        seen: list[str] = []

        with con:
            for it in items:
                seen.append(it.doc_id)
                file = it.file or FileRef(url="", filename="")
                con.execute(
                    """
                    INSERT INTO documents (
                        doc_id, course_id, course_name, section_name, module_id, module_name,
                        modname, title, kind, header_path, module_url, timemodified, text,
                        file_url, filename, filesize, mimetype, external_url, extractable,
                        skip_reason, first_seen, last_seen, tombstoned_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        course_name=excluded.course_name,
                        section_name=excluded.section_name,
                        module_name=excluded.module_name,
                        title=excluded.title,
                        kind=excluded.kind,
                        header_path=excluded.header_path,
                        module_url=excluded.module_url,
                        timemodified=excluded.timemodified,
                        text=excluded.text,
                        file_url=excluded.file_url,
                        filename=excluded.filename,
                        filesize=excluded.filesize,
                        mimetype=excluded.mimetype,
                        external_url=excluded.external_url,
                        extractable=excluded.extractable,
                        skip_reason=excluded.skip_reason,
                        last_seen=excluded.last_seen,
                        tombstoned_at=NULL
                    """,
                    (
                        it.doc_id,
                        it.course_id,
                        it.course_name,
                        it.section_name,
                        it.module_id,
                        it.module_name,
                        it.modname,
                        it.title,
                        str(it.kind),
                        json.dumps(it.header_path, ensure_ascii=False),
                        it.module_url,
                        it.timemodified,
                        it.text,
                        file.url or None,
                        file.filename or None,
                        file.filesize,
                        file.mimetype,
                        it.external_url,
                        int(it.extractable),
                        it.skip_reason,
                        stamp,
                        stamp,
                    ),
                )

            # AC-7: tombstone rather than delete, and drop the chunks so the content
            # stops being answerable immediately.
            placeholders = ",".join("?" * len(seen)) if seen else "''"
            gone = [
                row["doc_id"]
                for row in con.execute(
                    f"SELECT doc_id FROM documents WHERE tombstoned_at IS NULL "
                    f"AND doc_id NOT IN ({placeholders})",
                    seen,
                )
            ]
            for doc_id in gone:
                self._delete_chunks(con, doc_id)
            if gone:
                con.execute(
                    f"UPDATE documents SET tombstoned_at=? WHERE doc_id IN "
                    f"({','.join('?' * len(gone))})",
                    [stamp, *gone],
                )
                log.info("store.tombstoned", count=len(gone))

    def active_documents(self) -> list[Document]:
        return [
            self._to_document(r)
            for r in self.connection.execute(
                "SELECT * FROM documents WHERE tombstoned_at IS NULL ORDER BY doc_id"
            )
        ]

    def document(self, doc_id: str) -> Document:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"no such document: {doc_id}")
        return self._to_document(row)

    def documents_needing_extraction(self, *, extract_version: int) -> list[Document]:
        """New, changed, or extracted under an older extractor (AC-9)."""
        rows = self.connection.execute(
            """
            SELECT * FROM documents
            WHERE tombstoned_at IS NULL AND extractable = 1
              AND (extract_version IS NULL
                   OR extract_version < ?
                   OR extracted_timemodified IS NULL
                   OR timemodified != extracted_timemodified)
            ORDER BY doc_id
            """,
            (extract_version,),
        ).fetchall()
        return [self._to_document(r) for r in rows]

    def record_extraction(
        self,
        doc_id: str,
        *,
        text_sha256: str,
        extract_version: int,
        blob_sha256: str | None = None,
        now: int | None = None,
    ) -> None:
        stamp = now if now is not None else int(time.time())
        with self.connection as con:
            # Record the *timemodified we extracted*, not the wall clock. Comparing
            # Moodle's timestamps against our clock only works by coincidence and
            # breaks for backdated content.
            #
            # content_changed_at only advances when the extracted text actually
            # differs from what was stored before (or there was nothing stored yet).
            # It is the only trustworthy "last changed" signal external adapters
            # have — Moodle's own timemodified reflects when the *link* to a
            # TaskCards board or YouTube video was pasted in, not when that board
            # or video last changed. The comparison below reads the pre-update row
            # (SQLite evaluates every expression in an UPDATE against the old row),
            # so it is always comparing against the previous extraction, not this one.
            con.execute(
                "UPDATE documents SET text_sha256=?, extract_version=?, blob_sha256=?, "
                "extracted_at=?, extracted_timemodified=timemodified, "
                "content_changed_at = CASE "
                "  WHEN content_changed_at IS NULL OR text_sha256 IS NOT ? THEN ? "
                "  ELSE content_changed_at "
                "END "
                "WHERE doc_id=?",
                (text_sha256, extract_version, blob_sha256, stamp, text_sha256, stamp, doc_id),
            )

    def reset_extraction_for_empty_documents(self) -> int:
        """Mark documents that yielded no chunks as un-extracted, and return the count.

        Used when a new capability (OCR) makes previously unreadable documents
        readable. Scoping the reset to empty documents means enabling OCR costs 70
        re-extractions rather than 500.
        """
        with self.connection as con:
            cursor = con.execute(
                """
                UPDATE documents
                SET extract_version = NULL, extracted_timemodified = NULL
                WHERE tombstoned_at IS NULL AND extractable = 1
                  AND doc_id NOT IN (SELECT DISTINCT doc_id FROM chunks)
                """
            )
        return int(cursor.rowcount or 0)

    def reset_extraction_for_doc_ids(self, doc_ids: list[str]) -> int:
        """Mark specific documents as un-extracted, and return how many changed.

        Used when an alias file is edited: only the handful of named documents need
        reprocessing, not the whole corpus (spec 007 AC-22).
        """
        if not doc_ids:
            return 0
        with self.connection as con:
            marks = ",".join("?" * len(doc_ids))
            cursor = con.execute(
                f"UPDATE documents SET extract_version = NULL, extracted_timemodified = NULL "
                f"WHERE doc_id IN ({marks}) AND tombstoned_at IS NULL",
                doc_ids,
            )
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------ #
    # Blob cache
    # ------------------------------------------------------------------ #

    def put_blob(self, data: bytes) -> str:
        """Store bytes content-addressed; identical content is stored once (AC-10)."""
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic: a reader never sees a half-written blob
        with self.connection as con:
            con.execute(
                "INSERT OR IGNORE INTO blobs (sha256, size, created_at) VALUES (?,?,?)",
                (digest, len(data), int(time.time())),
            )
        return digest

    def blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / sha256[:2] / sha256

    def blob_count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM blobs").fetchone()[0])

    def unreferenced_blobs(self) -> list[str]:
        """Report orphans; never delete implicitly (AC-13)."""
        return [
            r[0]
            for r in self.connection.execute(
                "SELECT sha256 FROM blobs WHERE sha256 NOT IN "
                "(SELECT blob_sha256 FROM documents WHERE blob_sha256 IS NOT NULL)"
            )
        ]

    def record_fetch(
        self,
        url: str,
        *,
        sha256: str | None,
        etag: str | None = None,
        last_modified: str | None = None,
        moodle_timemodified: int | None = None,
        fetched_at: int | None = None,
        checked_at: int | None = None,
    ) -> None:
        with self.connection as con:
            con.execute(
                "INSERT INTO fetches (url, sha256, etag, last_modified, moodle_timemodified, "
                "fetched_at, checked_at, error) VALUES (?,?,?,?,?,?,?,NULL) "
                "ON CONFLICT(url) DO UPDATE SET sha256=excluded.sha256, etag=excluded.etag, "
                "last_modified=excluded.last_modified, "
                "moodle_timemodified=excluded.moodle_timemodified, "
                "fetched_at=excluded.fetched_at, checked_at=excluded.checked_at, error=NULL",
                (url, sha256, etag, last_modified, moodle_timemodified, fetched_at, checked_at),
            )

    def record_fetch_failure(self, url: str, *, error: str, checked_at: int | None = None) -> None:
        """Remember failures so a dead link is not retried on every sync (AC-12)."""
        with self.connection as con:
            con.execute(
                "INSERT INTO fetches (url, error, checked_at) VALUES (?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET error=excluded.error, "
                "checked_at=excluded.checked_at",
                (url, error, checked_at if checked_at is not None else int(time.time())),
            )

    def fetch_record(self, url: str) -> FetchRecord | None:
        row = self.connection.execute("SELECT * FROM fetches WHERE url=?", (url,)).fetchone()
        return FetchRecord(**dict(row)) if row else None

    def touch_fetch(self, url: str, *, checked_at: int) -> None:
        """Record that we revalidated and nothing had changed (a 304)."""
        with self.connection as con:
            con.execute("UPDATE fetches SET checked_at=? WHERE url=?", (checked_at, url))

    # ------------------------------------------------------------------ #
    # OCR cache
    # ------------------------------------------------------------------ #

    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
        row = self.connection.execute(
            "SELECT text FROM ocr_cache WHERE image_sha256=? AND model_tag=?",
            (image_sha256, model_tag),
        ).fetchone()
        return str(row[0]) if row else None

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None:
        """Persist a transcription so each scanned page is paid for exactly once."""
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO ocr_cache (image_sha256, model_tag, text, created_at) "
                "VALUES (?,?,?,?)",
                (image_sha256, model_tag, text, int(time.time())),
            )

    # ------------------------------------------------------------------ #
    # Chunks
    # ------------------------------------------------------------------ #

    def replace_chunks(
        self, doc_id: str, chunks: Sequence[tuple[str, dict[str, Any]]], *, header_text: str = ""
    ) -> list[int]:
        """Swap a document's chunks in one transaction (AC-14)."""
        con = self.connection
        ids: list[int] = []
        with con:
            self._delete_chunks(con, doc_id)
            for ordinal, (text, meta) in enumerate(chunks):
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                cur = con.execute(
                    "INSERT INTO chunks (doc_id, ordinal, text, header_text, page, text_sha256, "
                    "meta) VALUES (?,?,?,?,?,?,?)",
                    (
                        doc_id,
                        meta.get("ordinal", ordinal),
                        text,
                        meta.get("header_text", header_text),
                        meta.get("page"),
                        digest,
                        json.dumps(meta, ensure_ascii=False, default=str),
                    ),
                )
                chunk_id = int(cur.lastrowid or 0)
                ids.append(chunk_id)
                con.execute(
                    "INSERT INTO chunks_fts (rowid, text, header_text) VALUES (?,?,?)",
                    (chunk_id, text, meta.get("header_text", header_text)),
                )
        return ids

    @staticmethod
    def _delete_chunks(con: sqlite3.Connection, doc_id: str) -> None:
        """Remove a document's chunks from all three indexes together (AC-15)."""
        rows = con.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
        if not rows:
            return
        ids = [r["chunk_id"] for r in rows]
        marks = ",".join("?" * len(ids))
        # FTS5 external-content tables need the old value supplied on delete.
        con.executemany(
            "INSERT INTO chunks_fts (chunks_fts, rowid, text, header_text) "
            "VALUES ('delete', ?, ?, ?)",
            [
                (r["chunk_id"], r2["text"], r2["header_text"])
                for r, r2 in zip(
                    rows,
                    con.execute(
                        f"SELECT text, header_text FROM chunks WHERE chunk_id IN ({marks})",
                        ids,
                    ).fetchall(),
                    strict=True,
                )
            ],
        )
        con.execute(f"DELETE FROM chunks_vec WHERE chunk_id IN ({marks})", ids)
        con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def chunks_for(self, doc_id: str) -> list[Chunk]:
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY ordinal", (doc_id,)
        ).fetchall()
        return [
            Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                ordinal=r["ordinal"],
                text=r["text"],
                header_text=r["header_text"],
                page=r["page"],
                meta=json.loads(r["meta"]),
            )
            for r in rows
        ]

    def iter_chunks(self) -> Iterator[Chunk]:
        for r in self.connection.execute("SELECT * FROM chunks ORDER BY chunk_id"):
            yield Chunk(
                chunk_id=r["chunk_id"],
                doc_id=r["doc_id"],
                ordinal=r["ordinal"],
                text=r["text"],
                header_text=r["header_text"],
                page=r["page"],
                meta=json.loads(r["meta"]),
            )

    # ------------------------------------------------------------------ #
    # Matrix Moderator Messages
    # ------------------------------------------------------------------ #

    def index_matrix_message(
        self,
        room_id: str,
        event_id: str,
        sender: str,
        text: str,
        timemodified: int,
        *,
        room_name: str = "",
        max_history_per_room: int = 10,
        embedder: Any = None,
        now: int | None = None,
    ) -> list[int]:
        """Index a message from a room moderator with channel-scoped metadata and embed it."""
        stamp = now if now is not None else int(time.time())
        doc_id = f"matrix:{room_id}:{event_id}"
        room_label = room_name or room_id
        header_path = ["Matrix", room_label, f"Mitteilung von {sender}"]
        header_text = " › ".join(header_path)
        chunk_text = f"{header_text}\n\n{text}"
        module_url = f"https://matrix.to/#/{room_id}/{event_id}"
        filesize = len(text.encode("utf-8"))

        con = self.connection
        with con:
            con.execute(
                """
                INSERT INTO documents (
                    doc_id, course_id, course_name, section_name, module_id, module_name,
                    modname, title, kind, header_path, module_url, timemodified, text,
                    filesize, extractable, first_seen, last_seen, tombstoned_at, content_changed_at
                ) VALUES (
                    ?, 0, ?, ?, 0, ?,
                    'matrix_message', ?, 'inline', ?, ?, ?, ?,
                    ?, 1, ?, ?, NULL, ?
                )
                ON CONFLICT(doc_id) DO UPDATE SET
                    course_name=excluded.course_name,
                    section_name=excluded.section_name,
                    module_name=excluded.module_name,
                    title=excluded.title,
                    header_path=excluded.header_path,
                    module_url=excluded.module_url,
                    timemodified=excluded.timemodified,
                    text=excluded.text,
                    filesize=excluded.filesize,
                    last_seen=excluded.last_seen,
                    tombstoned_at=NULL,
                    content_changed_at=excluded.content_changed_at
                """,
                (
                    doc_id,
                    f"Matrix: {room_label}",
                    room_id,
                    f"Mitteilung von {sender}",
                    f"Mitteilung von {sender}",
                    json.dumps(header_path, ensure_ascii=False),
                    module_url,
                    timemodified,
                    text,
                    filesize,
                    stamp,
                    stamp,
                    stamp,
                ),
            )

        meta = {
            "header_text": header_text,
            "body": text,
            "room_id": room_id,
            "sender": sender,
            "event_id": event_id,
        }
        chunk_ids = self.replace_chunks(doc_id, [(chunk_text, meta)], header_text=header_text)

        if embedder is not None and chunk_ids:
            try:
                if hasattr(embedder, "embed_documents"):
                    vecs = embedder.embed_documents([chunk_text])
                    if vecs and vecs[0]:
                        self.set_embedding(chunk_ids[0], vecs[0])
                elif hasattr(embedder, "embed_query"):
                    vec = embedder.embed_query(chunk_text)
                    if vec:
                        self.set_embedding(chunk_ids[0], vec)
            except Exception as exc:
                log.warning("store.matrix_embed_failed", doc_id=doc_id, error=str(exc))

        if max_history_per_room > 0:
            self.prune_matrix_messages(
                room_id, max_history_per_room=max_history_per_room, now=stamp
            )

        return chunk_ids

    def prune_matrix_messages(
        self, room_id: str, *, max_history_per_room: int = 10, now: int | None = None
    ) -> list[str]:
        """Keep only the most recent N active moderator messages for a room."""
        stamp = now if now is not None else int(time.time())
        con = self.connection
        rows = con.execute(
            "SELECT doc_id FROM documents "
            "WHERE doc_id LIKE 'matrix:' || ? || ':%' AND tombstoned_at IS NULL "
            "ORDER BY timemodified DESC, rowid DESC",
            (room_id,),
        ).fetchall()

        if len(rows) <= max_history_per_room:
            return []

        excess = [r["doc_id"] for r in rows[max_history_per_room:]]
        with con:
            for old_doc_id in excess:
                self._delete_chunks(con, old_doc_id)
            marks = ",".join("?" * len(excess))
            con.execute(
                f"UPDATE documents SET tombstoned_at=? WHERE doc_id IN ({marks})",
                [stamp, *excess],
            )
        log.info("store.matrix_pruned", room=room_id, count=len(excess))
        return excess

    # ------------------------------------------------------------------ #
    # Vectors
    # ------------------------------------------------------------------ #

    def set_embedding(self, chunk_id: int, vector: Sequence[float]) -> None:
        """Store a chunk vector, normalised (AC-17).

        ``gemini-embedding-001`` returns un-normalised vectors when
        ``output_dimensionality`` is reduced — measured norm ≈ 0.58 at 768 dims.
        Normalising here means every consumer can treat the space as cosine.
        """
        normalised = normalise(vector)
        with self.connection as con:
            con.execute("DELETE FROM chunks_vec WHERE chunk_id=?", (chunk_id,))
            con.execute(
                "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?,?)",
                (chunk_id, _pack(normalised)),
            )

    def embedding_for(self, chunk_id: int) -> list[float] | None:
        row = self.connection.execute(
            "SELECT embedding FROM chunks_vec WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        return _unpack(row[0]) if row else None

    def cache_embedding(
        self, text_sha256: str, model: str, dim: int, task_type: str, vector: Sequence[float]
    ) -> None:
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(text_sha256, model, dim, task_type, vector, created_at) VALUES (?,?,?,?,?,?)",
                (text_sha256, model, dim, task_type, _pack(normalise(vector)), int(time.time())),
            )

    def cached_embedding(
        self, text_sha256: str, model: str, dim: int, task_type: str
    ) -> list[float] | None:
        row = self.connection.execute(
            "SELECT vector FROM embedding_cache WHERE text_sha256=? AND model=? AND dim=? "
            "AND task_type=?",
            (text_sha256, model, dim, task_type),
        ).fetchone()
        return _unpack(row[0]) if row else None

    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_document(row: sqlite3.Row) -> Document:
        data = dict(row)
        data["header_path"] = json.loads(data.get("header_path") or "[]")
        data["extractable"] = bool(data["extractable"])
        data.pop("extracted_at", None)
        data.pop("extracted_timemodified", None)
        data.pop("content_changed_at", None)
        return Document(**data)


def normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return list(vector)
    return [v / norm for v in vector]


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_EMBED_DIM",
    "SCHEMA_VERSION",
    "Chunk",
    "ContentKind",
    "Document",
    "FetchRecord",
    "Store",
    "StoreError",
    "StoreVersionError",
    "content_sha256",
    "normalise",
]
