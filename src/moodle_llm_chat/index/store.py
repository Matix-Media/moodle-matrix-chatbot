"""The encrypted, site-scoped store.

Two layers of encryption (see :mod:`moodle_llm_chat.crypto`):

1. The whole file is SQLCipher-encrypted, covering every table and index.
2. Every content-bearing column is additionally sealed with AES-GCM under a
   per-course (content), per-blob or per-user (chat) data key, bound by AAD to the
   exact row it belongs to. Ids, timestamps, kinds and sizes stay plain so SQL can
   filter and order on them; text never does.

FTS5 is *contentless*: the token index is populated from plaintext at insert time
(protected by layer 1) and no second plaintext copy of the chunk exists. Vectors are
scored with ``vec_distance_cosine`` over exactly the chunks a caller may see, so a
per-user access scope never costs recall.

All methods are synchronous and must be called from the thread that opened the
store (the event loop thread in the API).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import secrets
import struct
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Self

import structlog

from moodle_llm_chat.crypto.envelope import (
    StreamEncryptor,
    aad,
    decrypt_stream,
    open_box,
    seal,
)
from moodle_llm_chat.crypto.keys import KeyRing, Scope
from moodle_llm_chat.index.db import open_encrypted
from moodle_llm_chat.index.models import (
    Chunk,
    ChunkIn,
    Conversation,
    Course,
    CourseIn,
    Document,
    DocumentIn,
    DocumentKind,
    Job,
    Segment,
    SegmentIn,
    Site,
    SyncResult,
    Turn,
)

log = structlog.get_logger(__name__)

SCHEMA_VERSION = 2
#: A sync run with no activity for this long can no longer tombstone anything.
SYNC_STALE_AFTER_S = 24 * 3600

#: Placement fields kept in a document's sealed payload (the rest are plain columns).
_SEALED_PLACEMENT = ("section_name", "module_name", "title", "header_path", "module_url")


@dataclass(frozen=True)
class UpsertResult:
    #: Content changed: extract (or fetch) again.
    changed: list[str] = field(default_factory=list)
    #: Only the header path changed: re-chunk from the stored segments.
    relocated: list[str] = field(default_factory=list)


def _migrate(con: Any, from_version: int) -> None:
    """Bring an index written by an older build up to :data:`SCHEMA_VERSION`."""
    if from_version < 2:
        columns = {r[1] for r in con.execute("PRAGMA table_info(documents)").fetchall()}
        if "placement_hash" not in columns:
            con.execute("ALTER TABLE documents ADD COLUMN placement_hash TEXT")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS keys (
    scope TEXT NOT NULL, scope_id TEXT NOT NULL, wrapped BLOB NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY (scope, scope_id)
);

CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    secret_lookup TEXT NOT NULL UNIQUE,
    sealed_secret BLOB NOT NULL,
    prev_secret_lookup TEXT,
    sealed_prev_secret BLOB,
    prev_valid_until INTEGER,
    policy TEXT,
    ceilings TEXT,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    last_seen_at INTEGER
);

CREATE TABLE IF NOT EXISTS courses (
    site_id TEXT NOT NULL, course_id INTEGER NOT NULL,
    payload BLOB NOT NULL,
    visible INTEGER NOT NULL, startdate INTEGER NOT NULL, enddate INTEGER NOT NULL,
    timemodified INTEGER NOT NULL, sync_id TEXT, last_seen INTEGER NOT NULL,
    seen_seq INTEGER NOT NULL DEFAULT 0,
    tombstoned_at INTEGER,
    PRIMARY KEY (site_id, course_id)
);

CREATE TABLE IF NOT EXISTS documents (
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL,
    course_id INTEGER NOT NULL, section_id INTEGER NOT NULL, section_order INTEGER NOT NULL,
    cmid INTEGER NOT NULL, modname TEXT NOT NULL, kind TEXT NOT NULL,
    payload BLOB NOT NULL,
    mimetype TEXT, filesize INTEGER NOT NULL, blob_sha256 TEXT,
    timemodified INTEGER NOT NULL, content_hash TEXT NOT NULL,
    extract_version INTEGER, extracted_at INTEGER, skip_reason TEXT,
    char_count INTEGER NOT NULL DEFAULT 0, page_count INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, sync_id TEXT,
    seen_seq INTEGER NOT NULL DEFAULT 0,
    tombstoned_at INTEGER,
    placement_hash TEXT,
    PRIMARY KEY (site_id, doc_id)
);
CREATE INDEX IF NOT EXISTS documents_course ON documents(site_id, course_id);
CREATE INDEX IF NOT EXISTS documents_cmid ON documents(site_id, cmid);
CREATE INDEX IF NOT EXISTS documents_extract ON documents(extract_version);

CREATE TABLE IF NOT EXISTS sync_runs (
    site_id TEXT NOT NULL, sync_id TEXT NOT NULL, mode TEXT NOT NULL,
    state TEXT NOT NULL, started_at INTEGER NOT NULL, last_activity_at INTEGER NOT NULL,
    start_seq INTEGER NOT NULL DEFAULT 0,
    finished_at INTEGER,
    PRIMARY KEY (site_id, sync_id)
);

CREATE TABLE IF NOT EXISTS blobs (
    sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    course_id INTEGER NOT NULL, page INTEGER, chars INTEGER NOT NULL,
    sealed BLOB NOT NULL,
    PRIMARY KEY (site_id, doc_id, ordinal),
    FOREIGN KEY (site_id, doc_id) REFERENCES documents(site_id, doc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    course_id INTEGER NOT NULL, page INTEGER, text_sha256 TEXT NOT NULL,
    sealed BLOB NOT NULL,
    FOREIGN KEY (site_id, doc_id) REFERENCES documents(site_id, doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(site_id, doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, header_text, content='', contentless_delete=1,
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    lookup TEXT NOT NULL, key TEXT NOT NULL, vector BLOB NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (lookup, key)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, stage TEXT NOT NULL,
    state TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL,
    next_attempt_at INTEGER NOT NULL, enqueued_at INTEGER NOT NULL,
    started_at INTEGER, finished_at INTEGER, last_error TEXT,
    UNIQUE (site_id, doc_id, stage)
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(stage, state, priority, next_attempt_at);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, site_id TEXT NOT NULL, user_id INTEGER NOT NULL,
    course_id INTEGER, sealed_title BLOB, sealed_citations BLOB,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, archived_at INTEGER
);
CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(site_id, user_id, updated_at);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    client_msg_id TEXT, state TEXT NOT NULL,
    created_at INTEGER NOT NULL, finished_at INTEGER,
    UNIQUE (conversation_id, client_msg_id)
);
CREATE INDEX IF NOT EXISTS turns_state ON turns(state);

CREATE TABLE IF NOT EXISTS turn_events (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, turn_id TEXT NOT NULL, sealed BLOB NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);

CREATE TABLE IF NOT EXISTS ckpt_storage (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    sealed BLOB NOT NULL, PRIMARY KEY (thread_id, ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS ckpt_writes (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL, idx INTEGER NOT NULL, sealed BLOB NOT NULL,
    PRIMARY KEY (thread_id, ns, checkpoint_id, task_id, idx)
);
CREATE TABLE IF NOT EXISTS ckpt_blobs (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, channel TEXT NOT NULL, version TEXT NOT NULL,
    sealed BLOB NOT NULL, PRIMARY KEY (thread_id, ns, channel, version)
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, user_id INTEGER, conversation_id TEXT, turn_id TEXT,
    purpose TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL, estimated INTEGER NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_site_ts ON usage(site_id, purpose, ts);
CREATE INDEX IF NOT EXISTS usage_user_ts ON usage(site_id, user_id, ts);

CREATE TABLE IF NOT EXISTS used_jtis (
    site_id TEXT NOT NULL, jti TEXT NOT NULL, expires_at INTEGER NOT NULL,
    PRIMARY KEY (site_id, jti)
);

CREATE TABLE IF NOT EXISTS ocr_cache (
    lookup TEXT NOT NULL, model_tag TEXT NOT NULL, sealed BLOB NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY (lookup, model_tag)
);
"""


class StoreError(RuntimeError):
    """A store operation failed for a reason the caller should report."""


class SyncStateError(StoreError):
    """A sync run is unknown, stale, or in the wrong state."""


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _marks(values: Collection[Any]) -> str:
    return ",".join("?" * len(values))


class _HashingLimitedWriter:
    """Hashes and counts plaintext on its way into the encryptor."""

    def __init__(self, enc: StreamEncryptor, max_bytes: int | None) -> None:
        self._enc = enc
        self._max = max_bytes
        self.sha = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self.size += len(data)
        if self._max is not None and self.size > self._max:
            raise StoreError(f"blob too large (limit {self._max} bytes)")
        self.sha.update(data)
        self._enc.write(data)


class BlobWriter:
    """Streams an upload into an encrypted temp file; :meth:`commit` verifies and keeps it.

    Plaintext never touches disk: bytes are encrypted as they arrive.
    """

    def __init__(self, store: Store, expected_sha256: str, max_bytes: int | None) -> None:
        self._store = store
        self._sha = expected_sha256
        self._final = store.blob_path(expected_sha256)
        self._final.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self._final.with_name(f"{self._final.name}.{secrets.token_hex(4)}.tmp")
        self._fh: BinaryIO = self._tmp.open("wb")
        self._key_existed = store.wrapped_key(Scope.BLOB.value, expected_sha256) is not None
        dek = store.ring.dek(store, Scope.BLOB, expected_sha256)
        self._enc = StreamEncryptor(self._fh, dek, aad=aad("blob", expected_sha256))
        self._writer = _HashingLimitedWriter(self._enc, max_bytes)
        self._done = False

    def write(self, data: bytes) -> None:
        try:
            self._writer.write(data)
        except Exception:
            self.abort()
            raise

    def commit(self) -> None:
        try:
            self._enc.close()
            self._fh.close()
            if self._writer.sha.hexdigest() != self._sha:
                raise StoreError("blob digest does not match its sha256")
            self._tmp.replace(self._final)
        except Exception:
            self.abort()
            raise
        with self._store.connection as con:
            con.execute(
                "INSERT OR IGNORE INTO blobs (sha256, size, created_at) VALUES (?,?,?)",
                (self._sha, self._writer.size, int(time.time())),
            )
        self._done = True

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        if not self._fh.closed:
            self._fh.close()
        self._tmp.unlink(missing_ok=True)
        if not self._key_existed and not self._store.blob_exists(self._sha):
            self._store.ring.forget(self._store, Scope.BLOB, self._sha)


class Store:
    """Encrypted storage. Also the :class:`~moodle_llm_chat.crypto.keys.KeyStore`."""

    def __init__(
        self,
        path: Path | str,
        ring: KeyRing,
        *,
        embed_dim: int,
        blobs_dir: Path | None = None,
        reset_embeddings: bool = False,
    ) -> None:
        self.path = Path(path)
        self._reset_embeddings = reset_embeddings
        self.ring = ring
        self.embed_dim = embed_dim
        self.blobs_dir = blobs_dir or self.path.parent / "blobs"
        self._con: Any = None
        self._lookup_key = ring.derive("lookup")
        self._last_seq = 0

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
        con = open_encrypted(self.path, self.ring)
        try:
            con.executescript(_SCHEMA)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            version = int(meta.get("schema_version", SCHEMA_VERSION))
            if version > SCHEMA_VERSION:
                raise StoreError(
                    f"{self.path} has schema version {version}; this build supports "
                    f"{SCHEMA_VERSION}. Upgrade rather than risk corrupting the index."
                )
            _migrate(con, version)
            stored_dim = meta.get("embed_dim")
            if self._reset_embeddings:
                con.execute("DELETE FROM chunk_vectors")
                con.execute("DELETE FROM embedding_cache")
            elif stored_dim is not None and int(stored_dim) != self.embed_dim:
                raise StoreError(
                    f"the index was built with embedding dimension {stored_dim}, but "
                    f"{self.embed_dim} is configured. Run `moodle-llm-chat reembed` after "
                    "changing the embedding model."
                )
            for table in ("documents", "courses"):
                row = con.execute(f"SELECT coalesce(max(seen_seq), 0) FROM {table}").fetchone()
                self._last_seq = max(self._last_seq, int(row[0]))
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?), ('embed_dim', ?)",
                (str(SCHEMA_VERSION), str(self.embed_dim)),
            )
            con.commit()
        except Exception:
            con.close()
            raise
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self._con = con

    def close(self) -> None:
        if self._con is not None:
            self._con.commit()
            self._con.close()
            self._con = None

    @property
    def connection(self) -> Any:
        if self._con is None:
            raise StoreError("store is not open")
        return self._con

    def _seq(self) -> int:
        """A strictly increasing "seen" sequence: orders activity within one second.

        Full syncs delete what was not seen since they started; wall-clock seconds
        cannot order a push and a sync start that happen in the same second.
        """
        self._last_seq = max(self._last_seq + 1, time.time_ns())
        return self._last_seq

    def _lookup(self, value: str) -> str:
        return hmac.new(self._lookup_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ #
    # KeyStore protocol
    # ------------------------------------------------------------------ #

    def wrapped_key(self, scope: str, scope_id: str) -> bytes | None:
        row = self.connection.execute(
            "SELECT wrapped FROM keys WHERE scope=? AND scope_id=?", (scope, scope_id)
        ).fetchone()
        return bytes(row[0]) if row else None

    def store_wrapped_key(self, scope: str, scope_id: str, wrapped: bytes) -> None:
        with self.connection as con:
            con.execute(
                "INSERT INTO keys (scope, scope_id, wrapped, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET wrapped=excluded.wrapped",
                (scope, scope_id, wrapped, int(time.time())),
            )

    def delete_wrapped_key(self, scope: str, scope_id: str) -> None:
        with self.connection as con:
            con.execute("DELETE FROM keys WHERE scope=? AND scope_id=?", (scope, scope_id))

    def all_wrapped_keys(self) -> list[tuple[str, str, bytes]]:
        return [
            (r[0], r[1], bytes(r[2]))
            for r in self.connection.execute("SELECT scope, scope_id, wrapped FROM keys")
        ]

    # ------------------------------------------------------------------ #
    # Sealing helpers
    # ------------------------------------------------------------------ #

    def _course_dek(self, site_id: str, course_id: int) -> bytes:
        return self.ring.dek(self, Scope.CONTENT, f"{site_id}:{course_id}")

    def _site_dek(self, site_id: str) -> bytes:
        return self.ring.dek(self, Scope.CONTENT, f"site:{site_id}")

    def _seal_json(self, dek: bytes, value: Any, *, bound_to: bytes) -> bytes:
        return seal(dek, json.dumps(value, ensure_ascii=False), aad=bound_to)

    def _open_json(self, dek: bytes, box: bytes, *, bound_to: bytes) -> Any:
        return json.loads(open_box(dek, bytes(box), aad=bound_to))

    # ------------------------------------------------------------------ #
    # Sites
    # ------------------------------------------------------------------ #

    def add_site(self, site_id: str, *, name: str, url: str, now: int | None = None) -> str:
        """Register a Moodle site; returns its secret (shown once to the operator)."""
        if self.connection.execute("SELECT 1 FROM sites WHERE site_id=?", (site_id,)).fetchone():
            raise StoreError(f"site {site_id!r} already exists")
        secret = f"mlc_{secrets.token_urlsafe(32)}"
        sealed = seal(self._site_dek(site_id), secret, aad=aad("sites", site_id, "secret"))
        with self.connection as con:
            con.execute(
                "INSERT INTO sites (site_id, name, url, secret_lookup, sealed_secret, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (site_id, name, url.rstrip("/"), self._lookup(secret), sealed, _now(now)),
            )
        return secret

    def rotate_site_secret(self, site_id: str, *, grace_s: int, now: int | None = None) -> str:
        row = self._site_row(site_id)
        secret = f"mlc_{secrets.token_urlsafe(32)}"
        sealed = seal(self._site_dek(site_id), secret, aad=aad("sites", site_id, "secret"))
        old_plain = open_box(
            self._site_dek(site_id),
            bytes(row["sealed_secret"]),
            aad=aad("sites", site_id, "secret"),
        )
        sealed_prev = seal(self._site_dek(site_id), old_plain, aad=aad("sites", site_id, "prev"))
        with self.connection as con:
            con.execute(
                "UPDATE sites SET secret_lookup=?, sealed_secret=?, prev_secret_lookup=?, "
                "sealed_prev_secret=?, prev_valid_until=? WHERE site_id=?",
                (
                    self._lookup(secret),
                    sealed,
                    row["secret_lookup"],
                    sealed_prev,
                    _now(now) + grace_s,
                    site_id,
                ),
            )
        return secret

    def revoke_site(self, site_id: str, *, now: int | None = None) -> None:
        self._site_row(site_id)
        with self.connection as con:
            con.execute("UPDATE sites SET revoked_at=? WHERE site_id=?", (_now(now), site_id))

    def authenticate_site(self, secret: str, *, now: int | None = None) -> Site | None:
        """The site this secret belongs to, or ``None``. Lookup is by keyed hash."""
        lookup = self._lookup(secret)
        stamp = _now(now)
        row = self.connection.execute(
            "SELECT * FROM sites WHERE revoked_at IS NULL AND (secret_lookup=? OR "
            "(prev_secret_lookup=? AND prev_valid_until >= ?))",
            (lookup, lookup, stamp),
        ).fetchone()
        if row is None:
            return None
        # Defence in depth: compare in constant time even after the indexed lookup.
        if not (
            hmac.compare_digest(row["secret_lookup"], lookup)
            or hmac.compare_digest(row["prev_secret_lookup"] or "", lookup)
        ):
            return None
        return self._to_site(row)

    def touch_site(self, site_id: str, *, now: int | None = None) -> None:
        with self.connection as con:
            con.execute("UPDATE sites SET last_seen_at=? WHERE site_id=?", (_now(now), site_id))

    def consume_jti(
        self, site_id: str, jti: str, expires_at: int, *, now: int | None = None
    ) -> bool:
        """Record a chat token id as used; ``False`` if it was used before (replay)."""
        stamp = _now(now)
        with self.connection as con:
            con.execute("DELETE FROM used_jtis WHERE expires_at < ?", (stamp - 300,))
            cur = con.execute(
                "INSERT OR IGNORE INTO used_jtis (site_id, jti, expires_at) VALUES (?,?,?)",
                (site_id, jti, expires_at),
            )
        return bool(cur.rowcount)

    def site(self, site_id: str) -> Site:
        return self._to_site(self._site_row(site_id))

    def sites(self) -> list[Site]:
        return [
            self._to_site(r)
            for r in self.connection.execute("SELECT * FROM sites ORDER BY created_at, site_id")
        ]

    def site_secret(self, site_id: str) -> str:
        """The current secret, for authenticating callbacks to the plugin."""
        row = self._site_row(site_id)
        return open_box(
            self._site_dek(site_id),
            bytes(row["sealed_secret"]),
            aad=aad("sites", site_id, "secret"),
        )

    def site_secrets(self, site_id: str, *, now: int | None = None) -> list[str]:
        """Current secret first, then the previous one while its grace period lasts."""
        row = self._site_row(site_id)
        result = [self.site_secret(site_id)]
        if row["sealed_prev_secret"] is not None and (row["prev_valid_until"] or 0) >= _now(now):
            result.append(
                open_box(
                    self._site_dek(site_id),
                    bytes(row["sealed_prev_secret"]),
                    aad=aad("sites", site_id, "prev"),
                )
            )
        return result

    def _site_row(self, site_id: str) -> Any:
        row = self.connection.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
        if row is None:
            raise StoreError(f"no such site: {site_id!r}")
        return row

    @staticmethod
    def _to_site(row: Any) -> Site:
        return Site(
            site_id=row["site_id"],
            name=row["name"],
            url=row["url"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    # ------------------------------------------------------------------ #
    # Courses
    # ------------------------------------------------------------------ #

    def upsert_courses(
        self,
        site_id: str,
        courses: Iterable[CourseIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> None:
        stamp = _now(now)
        with self.connection as con:
            for c in courses:
                payload = self._seal_json(
                    self._course_dek(site_id, c.course_id),
                    {"fullname": c.fullname, "shortname": c.shortname},
                    bound_to=aad("courses", site_id, c.course_id),
                )
                con.execute(
                    "INSERT INTO courses (site_id, course_id, payload, visible, startdate, "
                    "enddate, timemodified, sync_id, last_seen, seen_seq, tombstoned_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(site_id, course_id) DO UPDATE SET payload=excluded.payload, "
                    "visible=excluded.visible, startdate=excluded.startdate, "
                    "enddate=excluded.enddate, timemodified=excluded.timemodified, "
                    "sync_id=COALESCE(excluded.sync_id, courses.sync_id), "
                    "last_seen=excluded.last_seen, seen_seq=excluded.seen_seq, tombstoned_at=NULL",
                    (
                        site_id,
                        c.course_id,
                        payload,
                        int(c.visible),
                        c.startdate,
                        c.enddate,
                        c.timemodified,
                        sync_id,
                        stamp,
                        self._seq(),
                    ),
                )
        if sync_id:
            self._touch_sync(site_id, sync_id, stamp)

    def courses(self, site_id: str, *, course_ids: Collection[int] | None = None) -> list[Course]:
        sql = "SELECT * FROM courses WHERE site_id=? AND tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        if course_ids is not None:
            if not course_ids:
                return []
            sql += f" AND course_id IN ({_marks(course_ids)})"
            params += list(course_ids)
        rows = self.connection.execute(sql + " ORDER BY course_id", params).fetchall()
        out = []
        for r in rows:
            p = self._open_json(
                self._course_dek(site_id, r["course_id"]),
                r["payload"],
                bound_to=aad("courses", site_id, r["course_id"]),
            )
            out.append(
                Course(
                    site_id=site_id,
                    course_id=r["course_id"],
                    fullname=p["fullname"],
                    shortname=p["shortname"],
                    visible=bool(r["visible"]),
                    startdate=r["startdate"],
                    enddate=r["enddate"],
                    timemodified=r["timemodified"],
                )
            )
        return out

    def tombstone_courses(
        self, site_id: str, course_ids: Collection[int], *, now: int | None = None
    ) -> list[str]:
        if not course_ids:
            return []
        stamp = _now(now)
        doc_ids = [
            r[0]
            for r in self.connection.execute(
                f"SELECT doc_id FROM documents WHERE site_id=? AND tombstoned_at IS NULL "
                f"AND course_id IN ({_marks(course_ids)})",
                [site_id, *course_ids],
            )
        ]
        self.tombstone_documents(site_id, doc_ids, now=stamp)
        with self.connection as con:
            con.execute(
                f"UPDATE courses SET tombstoned_at=? WHERE site_id=? "
                f"AND course_id IN ({_marks(course_ids)})",
                [stamp, site_id, *course_ids],
            )
        return doc_ids

    # ------------------------------------------------------------------ #
    # Documents
    # ------------------------------------------------------------------ #

    def upsert_documents(
        self,
        site_id: str,
        docs: Iterable[DocumentIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> list[str]:
        """Upsert documents; return the ids whose content changed (need extraction)."""
        return self.upsert_documents_detailed(site_id, docs, sync_id=sync_id, now=now).changed

    def upsert_documents_detailed(
        self,
        site_id: str,
        docs: Iterable[DocumentIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> UpsertResult:
        """Upsert documents; report which need extraction and which only re-chunking.

        A document whose content is unchanged keeps its extraction, but always takes
        its new placement: inserting a section shifts every later one, and a stale
        section order files documents under the wrong section.
        """
        stamp = _now(now)
        result = UpsertResult()
        con = self.connection
        with con:
            for d in docs:
                existing = con.execute(
                    "SELECT content_hash, tombstoned_at, section_id, section_order, payload, "
                    "placement_hash FROM documents WHERE site_id=? AND doc_id=?",
                    (site_id, d.doc_id),
                ).fetchone()
                payload_fields = self._document_payload(d)
                if (
                    existing is not None
                    and existing["content_hash"] == d.content_hash
                    and existing["tombstoned_at"] is None
                ):
                    stored = self._open_json(
                        self._course_dek(site_id, d.course_id),
                        existing["payload"],
                        bound_to=aad("documents", site_id, d.doc_id),
                    )
                    moved = (existing["section_id"], existing["section_order"]) != (
                        d.section_id,
                        d.section_order,
                    ) or any(stored.get(k) != payload_fields[k] for k in _SEALED_PLACEMENT)
                    if moved:
                        con.execute(
                            "UPDATE documents SET section_id=?, section_order=?, payload=? "
                            "WHERE site_id=? AND doc_id=?",
                            (
                                d.section_id,
                                d.section_order,
                                self._seal_document(site_id, d, payload_fields),
                                site_id,
                                d.doc_id,
                            ),
                        )
                        if stored.get("header_path") != payload_fields["header_path"]:
                            result.relocated.append(d.doc_id)
                    con.execute(
                        "UPDATE documents SET last_seen=?, seen_seq=?, "
                        "sync_id=COALESCE(?, sync_id), "
                        "placement_hash=COALESCE(?, placement_hash) WHERE site_id=? AND doc_id=?",
                        (stamp, self._seq(), sync_id, d.placement_hash, site_id, d.doc_id),
                    )
                    continue
                con.execute(
                    "INSERT INTO documents (site_id, doc_id, course_id, section_id, "
                    "section_order, cmid, modname, kind, payload, mimetype, filesize, "
                    "blob_sha256, timemodified, content_hash, extract_version, extracted_at, "
                    "skip_reason, first_seen, last_seen, sync_id, seen_seq, tombstoned_at, "
                    "placement_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?,?,?,NULL,?) "
                    "ON CONFLICT(site_id, doc_id) DO UPDATE SET course_id=excluded.course_id, "
                    "section_id=excluded.section_id, section_order=excluded.section_order, "
                    "cmid=excluded.cmid, modname=excluded.modname, kind=excluded.kind, "
                    "payload=excluded.payload, mimetype=excluded.mimetype, "
                    "filesize=excluded.filesize, blob_sha256=excluded.blob_sha256, "
                    "timemodified=excluded.timemodified, content_hash=excluded.content_hash, "
                    "extract_version=NULL, skip_reason=NULL, last_seen=excluded.last_seen, "
                    "seen_seq=excluded.seen_seq, "
                    "sync_id=COALESCE(excluded.sync_id, documents.sync_id), tombstoned_at=NULL, "
                    "placement_hash=excluded.placement_hash",
                    (
                        site_id,
                        d.doc_id,
                        d.course_id,
                        d.section_id,
                        d.section_order,
                        d.cmid,
                        d.modname,
                        d.kind.value,
                        self._seal_document(site_id, d, payload_fields),
                        d.file.mimetype if d.file else None,
                        d.file.filesize if d.file else len((d.text or "").encode("utf-8")),
                        d.file.sha256 if d.file else None,
                        d.timemodified,
                        d.content_hash,
                        stamp,
                        stamp,
                        sync_id,
                        self._seq(),
                        d.placement_hash,
                    ),
                )
                result.changed.append(d.doc_id)
        if sync_id:
            self._touch_sync(site_id, sync_id, stamp)
        return result

    @staticmethod
    def _document_payload(d: DocumentIn) -> dict[str, Any]:
        return {
            "title": d.title,
            "section_name": d.section_name,
            "module_name": d.module_name,
            "header_path": d.header_path,
            "module_url": d.module_url,
            "external_url": d.external_url,
            "text": d.text,
            "filename": d.file.filename if d.file else None,
        }

    def _seal_document(self, site_id: str, d: DocumentIn, payload: dict[str, Any]) -> bytes:
        return self._seal_json(
            self._course_dek(site_id, d.course_id),
            payload,
            bound_to=aad("documents", site_id, d.doc_id),
        )

    def document(self, site_id: str, doc_id: str) -> Document:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE site_id=? AND doc_id=?", (site_id, doc_id)
        ).fetchone()
        if row is None:
            raise StoreError(f"no such document: {doc_id!r}")
        return self._to_document(row)

    def live_documents(
        self,
        site_id: str,
        *,
        course_ids: Collection[int] | None = None,
        cmids: Collection[int] | None = None,
    ) -> list[Document]:
        sql = "SELECT * FROM documents WHERE site_id=? AND tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        for column, values in (("course_id", course_ids), ("cmid", cmids)):
            if values is not None:
                if not values:
                    return []
                sql += f" AND {column} IN ({_marks(values)})"
                params += list(values)
        sql += " ORDER BY course_id, section_order, doc_id"
        return [self._to_document(r) for r in self.connection.execute(sql, params)]

    def documents_needing_extraction(
        self, *, extract_version: int, limit: int | None = None
    ) -> list[Document]:
        sql = (
            "SELECT * FROM documents WHERE tombstoned_at IS NULL "
            "AND (extract_version IS NULL OR extract_version < ?) ORDER BY last_seen, doc_id"
        )
        params: list[Any] = [extract_version]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._to_document(r) for r in self.connection.execute(sql, params)]

    def record_extraction(
        self,
        site_id: str,
        doc_id: str,
        *,
        extract_version: int,
        skip_reason: str | None = None,
        now: int | None = None,
    ) -> None:
        with self.connection as con:
            con.execute(
                "UPDATE documents SET extract_version=?, extracted_at=?, skip_reason=? "
                "WHERE site_id=? AND doc_id=?",
                (extract_version, _now(now), skip_reason, site_id, doc_id),
            )

    def tombstone_documents(
        self, site_id: str, doc_ids: Collection[str], *, now: int | None = None
    ) -> None:
        """Tombstone and drop content, so it stops being answerable immediately."""
        if not doc_ids:
            return
        stamp = _now(now)
        with self.connection as con:
            for doc_id in doc_ids:
                self._delete_content(con, site_id, doc_id)
            con.execute(
                f"UPDATE documents SET tombstoned_at=? WHERE site_id=? "
                f"AND doc_id IN ({_marks(doc_ids)})",
                [stamp, site_id, *doc_ids],
            )
        log.info("store.tombstoned", site=site_id, count=len(doc_ids))

    def _to_document(self, row: Any) -> Document:
        site_id = row["site_id"]
        p = self._open_json(
            self._course_dek(site_id, row["course_id"]),
            row["payload"],
            bound_to=aad("documents", site_id, row["doc_id"]),
        )
        return Document(
            site_id=site_id,
            doc_id=row["doc_id"],
            course_id=row["course_id"],
            section_id=row["section_id"],
            section_name=p["section_name"],
            section_order=row["section_order"],
            cmid=row["cmid"],
            modname=row["modname"],
            module_name=p["module_name"],
            title=p["title"],
            kind=DocumentKind(row["kind"]),
            header_path=p["header_path"],
            module_url=p["module_url"],
            external_url=p["external_url"],
            text=p["text"],
            filename=p["filename"],
            mimetype=row["mimetype"],
            filesize=row["filesize"],
            blob_sha256=row["blob_sha256"],
            timemodified=row["timemodified"],
            content_hash=row["content_hash"],
            extract_version=row["extract_version"],
            extracted_at=row["extracted_at"],
            skip_reason=row["skip_reason"],
            char_count=row["char_count"],
            page_count=row["page_count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            tombstoned_at=row["tombstoned_at"],
        )

    # ------------------------------------------------------------------ #
    # Sync runs
    # ------------------------------------------------------------------ #

    def start_sync(self, site_id: str, sync_id: str, *, mode: str, now: int | None = None) -> None:
        if mode not in ("full", "incremental"):
            raise SyncStateError(f"unknown sync mode {mode!r}")
        stamp = _now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO sync_runs (site_id, sync_id, mode, state, started_at, "
                "last_activity_at, start_seq) VALUES (?,?,?,'open',?,?,?) "
                "ON CONFLICT(site_id, sync_id) DO UPDATE SET "
                "last_activity_at=excluded.last_activity_at",
                (site_id, sync_id, mode, stamp, stamp, self._seq()),
            )

    def apply_manifest(
        self,
        site_id: str,
        sync_id: str,
        entries: Mapping[str, str | tuple[str, str | None]],
        *,
        now: int | None = None,
    ) -> list[str]:
        """Mark every listed document as seen in this run; return the ids to send.

        An entry is a content hash, or ``(content_hash, placement_hash)``. A document
        is needed when its content changed or, if the plugin sends one, its placement
        hash differs from the stored one (a moved or renamed section).

        Listed-but-changed documents count as seen too: if the plugin then fails to
        push one, the old version is kept rather than deleted.
        """
        stamp = _now(now)
        self._open_run(site_id, sync_id, stamp)
        need: list[str] = []
        con = self.connection
        with con:
            for doc_id, entry in entries.items():
                content_hash, placement_hash = entry if isinstance(entry, tuple) else (entry, None)
                row = con.execute(
                    "SELECT content_hash, tombstoned_at, placement_hash FROM documents "
                    "WHERE site_id=? AND doc_id=?",
                    (site_id, doc_id),
                ).fetchone()
                if (
                    row is None
                    or row["tombstoned_at"] is not None
                    or row["content_hash"] != content_hash
                    or (placement_hash is not None and row["placement_hash"] != placement_hash)
                ):
                    need.append(doc_id)
                if row is not None and row["tombstoned_at"] is None:
                    con.execute(
                        "UPDATE documents SET sync_id=?, last_seen=?, seen_seq=? "
                        "WHERE site_id=? AND doc_id=?",
                        (sync_id, stamp, self._seq(), site_id, doc_id),
                    )
        self._touch_sync(site_id, sync_id, stamp)
        return sorted(need)

    def finish_sync(self, site_id: str, sync_id: str, *, now: int | None = None) -> SyncResult:
        stamp = _now(now)
        run = self.connection.execute(
            "SELECT * FROM sync_runs WHERE site_id=? AND sync_id=?", (site_id, sync_id)
        ).fetchone()
        if run is None:
            raise SyncStateError(f"unknown sync run {sync_id!r}")
        if run["state"] == "finished":
            # A retried finish after a lost response: already done, nothing more to do.
            return SyncResult(
                sync_id=sync_id, mode=run["mode"], tombstoned=[], courses_tombstoned=[]
            )
        if run["state"] == "stale" or stamp - run["last_activity_at"] > SYNC_STALE_AFTER_S:
            with self.connection as con:
                con.execute(
                    "UPDATE sync_runs SET state='stale' WHERE site_id=? AND sync_id=?",
                    (site_id, sync_id),
                )
            raise SyncStateError(f"sync run {sync_id!r} is stale; start a new full sync")

        tombstoned: list[str] = []
        courses_gone: list[int] = []
        if run["mode"] == "full":
            # "Seen in this run" = listed in its manifest or pushed since it started.
            # The plugin does not tag pushes with the sync id, so activity order decides.
            started = run["start_seq"]
            tombstoned = [
                r[0]
                for r in self.connection.execute(
                    "SELECT doc_id FROM documents WHERE site_id=? AND tombstoned_at IS NULL "
                    "AND seen_seq < ? AND (sync_id IS NULL OR sync_id != ?) ORDER BY doc_id",
                    (site_id, started, sync_id),
                )
            ]
            self.tombstone_documents(site_id, tombstoned, now=stamp)
            courses_gone = [
                r[0]
                for r in self.connection.execute(
                    "SELECT course_id FROM courses WHERE site_id=? AND tombstoned_at IS NULL "
                    "AND seen_seq < ? AND (sync_id IS NULL OR sync_id != ?)",
                    (site_id, started, sync_id),
                )
            ]
            self.tombstone_courses(site_id, courses_gone, now=stamp)
        with self.connection as con:
            con.execute(
                "UPDATE sync_runs SET state='finished', finished_at=? "
                "WHERE site_id=? AND sync_id=?",
                (stamp, site_id, sync_id),
            )
        return SyncResult(
            sync_id=sync_id,
            mode=run["mode"],
            tombstoned=tombstoned,
            courses_tombstoned=courses_gone,
        )

    def has_open_full_sync(self, site_id: str, *, now: int | None = None) -> bool:
        cutoff = _now(now) - SYNC_STALE_AFTER_S
        return (
            self.connection.execute(
                "SELECT 1 FROM sync_runs WHERE site_id=? AND mode='full' AND state='open' "
                "AND last_activity_at >= ? LIMIT 1",
                (site_id, cutoff),
            ).fetchone()
            is not None
        )

    def _open_run(self, site_id: str, sync_id: str, stamp: int) -> None:
        run = self.connection.execute(
            "SELECT state FROM sync_runs WHERE site_id=? AND sync_id=?", (site_id, sync_id)
        ).fetchone()
        if run is None or run["state"] != "open":
            raise SyncStateError(f"sync run {sync_id!r} is not open")

    def _touch_sync(self, site_id: str, sync_id: str, stamp: int) -> None:
        with self.connection as con:
            con.execute(
                "UPDATE sync_runs SET last_activity_at=? WHERE site_id=? AND sync_id=? "
                "AND state='open'",
                (stamp, site_id, sync_id),
            )

    # ------------------------------------------------------------------ #
    # Blobs
    # ------------------------------------------------------------------ #

    def blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / sha256[:2] / f"{sha256}.enc"

    def blob_exists(self, sha256: str) -> bool:
        return (
            self.connection.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha256,)).fetchone()
            is not None
            and self.blob_path(sha256).exists()
        )

    def documents_with_blob(self, site_id: str, sha256: str) -> list[str]:
        return [
            r[0]
            for r in self.connection.execute(
                "SELECT doc_id FROM documents WHERE site_id=? AND blob_sha256=? "
                "AND tombstoned_at IS NULL ORDER BY doc_id",
                (site_id, sha256),
            )
        ]

    def blob_referenced(self, site_id: str, sha256: str) -> bool:
        """Whether a live document of ``site_id`` points at this blob."""
        return (
            self.connection.execute(
                "SELECT 1 FROM documents WHERE site_id=? AND blob_sha256=? "
                "AND tombstoned_at IS NULL LIMIT 1",
                (site_id, sha256),
            ).fetchone()
            is not None
        )

    def missing_blobs(self, shas: Iterable[str]) -> list[str]:
        return [s for s in dict.fromkeys(shas) if not self.blob_exists(s)]

    def blob_writer(self, expected_sha256: str, *, max_bytes: int | None = None) -> BlobWriter:
        return BlobWriter(self, expected_sha256, max_bytes)

    def put_blob(
        self, source: BinaryIO, *, expected_sha256: str, max_bytes: int | None = None
    ) -> None:
        if self.blob_exists(expected_sha256):
            return
        writer = self.blob_writer(expected_sha256, max_bytes=max_bytes)
        while chunk := source.read(1024 * 1024):
            writer.write(chunk)
        writer.commit()

    def delete_orphan_blobs(self) -> list[str]:
        """Delete blobs no live document references; their keys are shredded too."""
        orphans = [
            r[0]
            for r in self.connection.execute(
                "SELECT sha256 FROM blobs WHERE sha256 NOT IN (SELECT blob_sha256 FROM documents "
                "WHERE blob_sha256 IS NOT NULL AND tombstoned_at IS NULL) ORDER BY sha256"
            )
        ]
        for sha in orphans:
            self.blob_path(sha).unlink(missing_ok=True)
            with self.connection as con:
                con.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
            self.ring.forget(self, Scope.BLOB, sha)
        if orphans:
            log.info("store.orphan_blobs_deleted", count=len(orphans))
        return orphans

    def read_blob(self, sha256: str) -> bytes:
        if not self.blob_exists(sha256):
            raise StoreError(f"no such blob: {sha256}")
        dek = self.ring.dek(self, Scope.BLOB, sha256)
        out = io.BytesIO()
        with self.blob_path(sha256).open("rb") as fh:
            decrypt_stream(fh, out, dek, aad=aad("blob", sha256))
        return out.getvalue()

    # ------------------------------------------------------------------ #
    # Extracted content: segments, chunks, vectors
    # ------------------------------------------------------------------ #

    def replace_content(
        self,
        site_id: str,
        doc_id: str,
        segments: Sequence[SegmentIn],
        chunks: Sequence[ChunkIn],
    ) -> list[int]:
        """Atomically swap a document's segments and chunks; returns new chunk ids."""
        row = self.connection.execute(
            "SELECT course_id FROM documents WHERE site_id=? AND doc_id=?", (site_id, doc_id)
        ).fetchone()
        if row is None:
            raise StoreError(f"no such document: {doc_id!r}")
        course_id = row["course_id"]
        dek = self._course_dek(site_id, course_id)
        ids: list[int] = []
        con = self.connection
        with con:
            self._delete_content(con, site_id, doc_id)
            for s in segments:
                con.execute(
                    "INSERT INTO segments (site_id, doc_id, ordinal, course_id, page, chars, "
                    "sealed) VALUES (?,?,?,?,?,?,?)",
                    (
                        site_id,
                        doc_id,
                        s.ordinal,
                        course_id,
                        s.page,
                        len(s.text),
                        self._seal_json(
                            dek,
                            {"text": s.text, "label": s.label},
                            bound_to=aad("segments", site_id, doc_id, s.ordinal),
                        ),
                    ),
                )
            for c in chunks:
                cur = con.execute(
                    "INSERT INTO chunks (site_id, doc_id, ordinal, course_id, page, text_sha256, "
                    "sealed) VALUES (?,?,?,?,?,?,?)",
                    (
                        site_id,
                        doc_id,
                        c.ordinal,
                        course_id,
                        c.page,
                        hashlib.sha256(c.text.encode("utf-8")).hexdigest(),
                        self._seal_json(
                            dek,
                            {"text": c.text, "header_text": c.header_text},
                            bound_to=aad("chunks", site_id, doc_id, c.ordinal),
                        ),
                    ),
                )
                chunk_id = int(cur.lastrowid)
                ids.append(chunk_id)
                con.execute(
                    "INSERT INTO chunks_fts (rowid, text, header_text) VALUES (?,?,?)",
                    (chunk_id, c.text, c.header_text),
                )
            pages = {s.page for s in segments if s.page is not None}
            con.execute(
                "UPDATE documents SET char_count=?, page_count=? WHERE site_id=? AND doc_id=?",
                (sum(len(s.text) for s in segments), len(pages), site_id, doc_id),
            )
        return ids

    @staticmethod
    def _delete_content(con: Any, site_id: str, doc_id: str) -> None:
        ids = [
            r[0]
            for r in con.execute(
                "SELECT chunk_id FROM chunks WHERE site_id=? AND doc_id=?", (site_id, doc_id)
            )
        ]
        if ids:
            con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({_marks(ids)})", ids)
            con.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({_marks(ids)})", ids)
        con.execute("DELETE FROM chunks WHERE site_id=? AND doc_id=?", (site_id, doc_id))
        con.execute("DELETE FROM segments WHERE site_id=? AND doc_id=?", (site_id, doc_id))

    def segments(self, site_id: str, doc_id: str) -> list[Segment]:
        rows = self.connection.execute(
            "SELECT * FROM segments WHERE site_id=? AND doc_id=? ORDER BY ordinal",
            (site_id, doc_id),
        ).fetchall()
        out = []
        for r in rows:
            p = self._open_json(
                self._course_dek(site_id, r["course_id"]),
                r["sealed"],
                bound_to=aad("segments", site_id, doc_id, r["ordinal"]),
            )
            out.append(
                Segment(
                    doc_id=doc_id,
                    ordinal=r["ordinal"],
                    text=p["text"],
                    page=r["page"],
                    label=p["label"],
                )
            )
        return out

    def chunks(self, site_id: str, chunk_ids: Sequence[int]) -> list[Chunk]:
        """Decrypted chunks of ``site_id``, in the order of ``chunk_ids``."""
        if not chunk_ids:
            return []
        rows = {
            r["chunk_id"]: r
            for r in self.connection.execute(
                f"SELECT * FROM chunks WHERE site_id=? AND chunk_id IN ({_marks(chunk_ids)})",
                [site_id, *chunk_ids],
            )
        }
        return [self._to_chunk(rows[i]) for i in chunk_ids if i in rows]

    def chunks_for_document(self, site_id: str, doc_id: str) -> list[Chunk]:
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE site_id=? AND doc_id=? ORDER BY ordinal", (site_id, doc_id)
        ).fetchall()
        return [self._to_chunk(r) for r in rows]

    def chunk_count(self, site_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT count(*) FROM chunks WHERE site_id=?", (site_id,)
            ).fetchone()[0]
        )

    def _to_chunk(self, row: Any) -> Chunk:
        site_id = row["site_id"]
        p = self._open_json(
            self._course_dek(site_id, row["course_id"]),
            row["sealed"],
            bound_to=aad("chunks", site_id, row["doc_id"], row["ordinal"]),
        )
        return Chunk(
            chunk_id=row["chunk_id"],
            site_id=site_id,
            doc_id=row["doc_id"],
            ordinal=row["ordinal"],
            text=p["text"],
            header_text=p["header_text"],
            page=row["page"],
        )

    def _scope_sql(
        self,
        site_id: str,
        *,
        cmids: Collection[int] | None,
        since: int | None,
        until: int | None,
        doc_ids: Collection[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """WHERE fragment (on aliases ``c``/``d``) restricting chunks to a scope."""
        sql = " c.site_id=? AND d.tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        if cmids is not None:
            sql += f" AND d.cmid IN ({_marks(cmids) or 'NULL'})"
            params += list(cmids)
        if doc_ids is not None:
            sql += f" AND d.doc_id IN ({_marks(doc_ids) or 'NULL'})"
            params += list(doc_ids)
        if since is not None:
            sql += " AND d.timemodified >= ?"
            params.append(since)
        if until is not None:
            sql += " AND d.timemodified <= ?"
            params.append(until)
        return sql, params

    def fts_chunk_ids(
        self,
        site_id: str,
        match: str,
        *,
        limit: int,
        cmids: Collection[int] | None = None,
        doc_ids: Collection[str] | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> list[int]:
        """BM25-ranked chunk ids for an FTS5 MATCH expression, within a scope."""
        where, params = self._scope_sql(
            site_id, cmids=cmids, since=since, until=until, doc_ids=doc_ids
        )
        rows = self.connection.execute(
            "SELECT c.chunk_id FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.rowid "
            "JOIN documents d ON d.site_id = c.site_id AND d.doc_id = c.doc_id "
            f"WHERE chunks_fts MATCH ? AND{where} ORDER BY f.rank LIMIT ?",
            [match, *params, limit],
        ).fetchall()
        return [r[0] for r in rows]

    def vector_chunk_ids(
        self,
        site_id: str,
        query_vector: Sequence[float],
        *,
        limit: int,
        cmids: Collection[int] | None = None,
        doc_ids: Collection[str] | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> list[int]:
        """Exact cosine nearest neighbours among exactly the chunks in scope."""
        where, params = self._scope_sql(
            site_id, cmids=cmids, since=since, until=until, doc_ids=doc_ids
        )
        rows = self.connection.execute(
            "SELECT c.chunk_id, vec_distance_cosine(v.embedding, ?) AS dist "
            "FROM chunk_vectors v JOIN chunks c ON c.chunk_id = v.chunk_id "
            "JOIN documents d ON d.site_id = c.site_id AND d.doc_id = c.doc_id "
            f"WHERE{where} ORDER BY dist LIMIT ?",
            [_pack(query_vector), *params, limit],
        ).fetchall()
        return [r[0] for r in rows]

    def chunks_without_vectors(
        self,
        *,
        limit: int,
        exclude_ids: Collection[int] = (),
        exclude_sites: Collection[str] = (),
    ) -> list[Chunk]:
        sql = (
            "SELECT c.* FROM chunks c LEFT JOIN chunk_vectors v ON v.chunk_id = c.chunk_id "
            "WHERE v.chunk_id IS NULL"
        )
        params: list[Any] = []
        if exclude_sites:
            sql += f" AND c.site_id NOT IN ({_marks(exclude_sites)})"
            params += list(exclude_sites)
        if exclude_ids:
            sql += f" AND c.chunk_id NOT IN ({_marks(exclude_ids)})"
            params += list(exclude_ids)
        rows = self.connection.execute(
            sql + " ORDER BY c.chunk_id LIMIT ?", [*params, limit]
        ).fetchall()
        return [self._to_chunk(r) for r in rows]

    def sites_with_pending_vectors(self) -> list[str]:
        return [
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT c.site_id FROM chunks c LEFT JOIN chunk_vectors v "
                "ON v.chunk_id = c.chunk_id WHERE v.chunk_id IS NULL"
            )
        ]

    def pending_vector_count(self) -> int:
        return int(
            self.connection.execute(
                "SELECT count(*) FROM chunks c LEFT JOIN chunk_vectors v "
                "ON v.chunk_id = c.chunk_id WHERE v.chunk_id IS NULL"
            ).fetchone()[0]
        )

    def set_embedding(self, chunk_id: int, vector: Sequence[float]) -> None:
        if len(vector) != self.embed_dim:
            raise StoreError(f"vector has {len(vector)} dimensions, expected {self.embed_dim}")
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO chunk_vectors (chunk_id, embedding) VALUES (?,?)",
                (chunk_id, _pack(vector)),
            )

    def reset_extraction(self, *, only_skipped: bool = False) -> int:
        """Mark documents for re-processing; returns how many changed."""
        sql = (
            "UPDATE documents SET extract_version=NULL, skip_reason=NULL "
            "WHERE tombstoned_at IS NULL AND extract_version IS NOT NULL"
        )
        if only_skipped:
            sql += " AND skip_reason IS NOT NULL"
        with self.connection as con:
            n = con.execute(sql).rowcount
        return int(n or 0)

    def rotate_master_key(self, new_ring: KeyRing) -> int:
        """Re-wrap every data key under ``new_ring`` and re-key the database file.

        Payloads are not re-encrypted: their data keys do not change. Returns the
        number of re-wrapped keys. The old master key no longer opens the store.
        """
        count = new_ring.rewrap_all(self, previous=self.ring)
        self.connection.execute(f'PRAGMA rekey = "{new_ring.sqlcipher_pragma_value()}"')
        self.ring = new_ring
        self._lookup_key = new_ring.derive("lookup")
        self._rehash_lookups()
        return count

    def _rehash_lookups(self) -> None:
        """Keyed lookups derive from the master key, so they are recomputed on rotation."""
        con = self.connection
        with con:
            for row in con.execute("SELECT site_id FROM sites").fetchall():
                current = self.site_secret(row[0])
                con.execute(
                    "UPDATE sites SET secret_lookup=?, prev_secret_lookup=NULL, "
                    "sealed_prev_secret=NULL, prev_valid_until=NULL WHERE site_id=?",
                    (self._lookup(current), row[0]),
                )
            # Caches are cheap to rebuild and keyed by the old lookup key.
            con.execute("DELETE FROM embedding_cache")
            con.execute("DELETE FROM ocr_cache")

    def clear_embeddings(self) -> int:
        with self.connection as con:
            n = con.execute("DELETE FROM chunk_vectors").rowcount
            con.execute("DELETE FROM embedding_cache")
        return int(n or 0)

    # ------------------------------------------------------------------ #
    # Caches (EmbeddingCache / OCRCache protocols)
    # ------------------------------------------------------------------ #

    def cached_embedding(self, text_sha256: str, key: str) -> list[float] | None:
        row = self.connection.execute(
            "SELECT vector FROM embedding_cache WHERE lookup=? AND key=?",
            (self._lookup(text_sha256), key),
        ).fetchone()
        return _unpack(bytes(row[0])) if row else None

    def cache_embedding(self, text_sha256: str, key: str, vector: list[float]) -> None:
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO embedding_cache (lookup, key, vector, created_at) "
                "VALUES (?,?,?,?)",
                (self._lookup(text_sha256), key, _pack(vector), int(time.time())),
            )

    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
        lookup = self._lookup(image_sha256)
        row = self.connection.execute(
            "SELECT sealed FROM ocr_cache WHERE lookup=? AND model_tag=?", (lookup, model_tag)
        ).fetchone()
        if row is None:
            return None
        dek = self.ring.dek(self, Scope.CONTENT, "ocr-cache")
        return open_box(dek, bytes(row[0]), aad=aad("ocr", lookup, model_tag))

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None:
        lookup = self._lookup(image_sha256)
        dek = self.ring.dek(self, Scope.CONTENT, "ocr-cache")
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO ocr_cache (lookup, model_tag, sealed, created_at) "
                "VALUES (?,?,?,?)",
                (
                    lookup,
                    model_tag,
                    seal(dek, text, aad=aad("ocr", lookup, model_tag)),
                    int(time.time()),
                ),
            )

    # ------------------------------------------------------------------ #
    # Jobs (the processing pipeline's persisted queue)
    # ------------------------------------------------------------------ #

    def requeue_failed_jobs(
        self, stage: str, *, older_than_s: int, priority: int = 1, now: int | None = None
    ) -> int:
        """Re-arm ``stage`` jobs that ran out of retries at least ``older_than_s`` ago.

        Only state ``failed`` (temporary errors that exhausted their attempts) is
        re-armed; ``skipped`` jobs failed for good (404, too large, unsupported).
        Documents removed since are left alone.
        """
        stamp = _now(now)
        with self.connection as con:
            n = con.execute(
                "UPDATE jobs SET state='queued', attempts=0, priority=?, next_attempt_at=?, "
                "enqueued_at=? WHERE stage=? AND state='failed' AND finished_at<=? "
                "AND EXISTS (SELECT 1 FROM documents d WHERE d.site_id=jobs.site_id "
                "  AND d.doc_id=jobs.doc_id AND d.tombstoned_at IS NULL)",
                (priority, stamp, stamp, stage, stamp - older_than_s),
            ).rowcount
        return int(n or 0)

    def enqueue_job(
        self,
        site_id: str,
        doc_id: str,
        stage: str,
        *,
        priority: int = 0,
        now: int | None = None,
    ) -> None:
        """Queue ``stage`` for a document. Re-enqueueing resets attempts.

        While already queued, the more urgent (lower) priority wins; a finished job
        is re-armed with the new priority.
        """
        stamp = _now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO jobs (site_id, doc_id, stage, state, priority, attempts, "
                "next_attempt_at, enqueued_at) VALUES (?,?,?,'queued',?,0,?,?) "
                "ON CONFLICT(site_id, doc_id, stage) DO UPDATE SET "
                "priority = CASE WHEN jobs.state='queued' "
                "  THEN min(jobs.priority, excluded.priority) ELSE excluded.priority END, "
                "state='queued', attempts=0, next_attempt_at=excluded.next_attempt_at, "
                "enqueued_at=CASE WHEN jobs.state='queued' THEN jobs.enqueued_at "
                "  ELSE excluded.enqueued_at END, "
                "last_error=NULL, finished_at=NULL",
                (site_id, doc_id, stage, priority, stamp, stamp),
            )

    def claim_jobs(self, stage: str, *, limit: int, now: int | None = None) -> list[Job]:
        if limit <= 0:
            return []
        stamp = _now(now)
        con = self.connection
        with con:
            rows = con.execute(
                "SELECT * FROM jobs WHERE stage=? AND state='queued' AND next_attempt_at<=? "
                "ORDER BY priority, next_attempt_at, id LIMIT ?",
                (stage, stamp, limit),
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                con.execute(
                    f"UPDATE jobs SET state='running', started_at=? WHERE id IN ({_marks(ids)})",
                    [stamp, *ids],
                )
        return [self._to_job(r) for r in rows]

    def finish_job(
        self, job_id: int, *, state: str, error: str | None = None, now: int | None = None
    ) -> None:
        if state not in ("done", "skipped", "failed", "waiting"):
            raise StoreError(f"invalid final job state {state!r}")
        with self.connection as con:
            con.execute(
                "UPDATE jobs SET state=?, finished_at=?, last_error=? WHERE id=?",
                (state, _now(now), error, job_id),
            )

    def retry_job(
        self,
        job_id: int,
        *,
        delay_s: float,
        error: str,
        max_attempts: int,
        now: int | None = None,
    ) -> bool:
        """Schedule another attempt; returns ``False`` once attempts are exhausted."""
        stamp = _now(now)
        row = self.connection.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        attempts = int(row[0]) + 1 if row else max_attempts
        with self.connection as con:
            if attempts >= max_attempts:
                con.execute(
                    "UPDATE jobs SET state='failed', attempts=?, finished_at=?, last_error=? "
                    "WHERE id=?",
                    (attempts, stamp, error, job_id),
                )
                return False
            con.execute(
                "UPDATE jobs SET state='queued', attempts=?, next_attempt_at=?, last_error=? "
                "WHERE id=?",
                (attempts, stamp + int(delay_s), error, job_id),
            )
        return True

    def reset_running_jobs(self) -> int:
        """Crash recovery: anything 'running' at startup never finished."""
        with self.connection as con:
            n = con.execute("UPDATE jobs SET state='queued' WHERE state='running'").rowcount
        return int(n or 0)

    def has_due_jobs(self, *, now: int | None = None) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM jobs WHERE state IN ('queued','running') "
                "AND (state='running' OR next_attempt_at<=?) LIMIT 1",
                (_now(now),),
            ).fetchone()
            is not None
        )

    def next_job_due_at(self) -> int | None:
        row = self.connection.execute(
            "SELECT min(next_attempt_at) FROM jobs WHERE state='queued'"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def job_counts(self, *, now: int | None = None) -> dict[str, dict[str, int]]:
        stamp = _now(now)
        out: dict[str, dict[str, int]] = {}
        for r in self.connection.execute(
            "SELECT stage, state, count(*) AS n, min(enqueued_at) AS oldest "
            "FROM jobs GROUP BY stage, state"
        ):
            entry = out.setdefault(r["stage"], {})
            entry[r["state"]] = int(r["n"])
            if r["state"] == "queued":
                entry["oldest_queued_age_s"] = stamp - int(r["oldest"])
        return out

    def failed_jobs(self, *, stage: str | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE state='failed'"
        params: list[Any] = []
        if stage:
            sql += " AND stage=?"
            params.append(stage)
        return [self._to_job(r) for r in self.connection.execute(sql + " ORDER BY id", params)]

    @staticmethod
    def _to_job(row: Any) -> Job:
        return Job(
            id=row["id"],
            site_id=row["site_id"],
            doc_id=row["doc_id"],
            stage=row["stage"],
            state=row["state"],
            priority=row["priority"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            last_error=row["last_error"],
        )

    # ------------------------------------------------------------------ #
    # Conversations (sealed with the owner's chat key)
    # ------------------------------------------------------------------ #

    def chat_dek(self, site_id: str, user_id: int) -> bytes:
        return self.ring.dek(self, Scope.CHAT, f"{site_id}:{user_id}")

    def create_conversation(
        self,
        site_id: str,
        user_id: int,
        *,
        course_id: int | None = None,
        now: int | None = None,
    ) -> str:
        conv_id = secrets.token_urlsafe(12)
        stamp = _now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO conversations (id, site_id, user_id, course_id, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?)",
                (conv_id, site_id, user_id, course_id, stamp, stamp),
            )
        return conv_id

    def _conversation_row(self, site_id: str, user_id: int, conv_id: str) -> Any:
        row = self.connection.execute(
            "SELECT * FROM conversations WHERE id=? AND site_id=? AND user_id=? "
            "AND archived_at IS NULL",
            (conv_id, site_id, user_id),
        ).fetchone()
        if row is None:
            raise StoreError("no such conversation")
        return row

    def _to_conversation(self, row: Any) -> Conversation:
        title = ""
        if row["sealed_title"] is not None:
            title = open_box(
                self.chat_dek(row["site_id"], row["user_id"]),
                bytes(row["sealed_title"]),
                aad=aad("conversations", row["id"], "title"),
            )
        return Conversation(
            id=row["id"],
            site_id=row["site_id"],
            user_id=row["user_id"],
            course_id=row["course_id"],
            title=title,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def conversation(self, site_id: str, user_id: int, conv_id: str) -> Conversation:
        return self._to_conversation(self._conversation_row(site_id, user_id, conv_id))

    def conversations(self, site_id: str, user_id: int, *, limit: int = 100) -> list[Conversation]:
        rows = self.connection.execute(
            "SELECT * FROM conversations WHERE site_id=? AND user_id=? AND archived_at IS NULL "
            "ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (site_id, user_id, limit),
        ).fetchall()
        return [self._to_conversation(r) for r in rows]

    def set_conversation_title(self, site_id: str, user_id: int, conv_id: str, title: str) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        sealed = seal(
            self.chat_dek(site_id, user_id), title[:200], aad=aad("conversations", conv_id, "title")
        )
        with self.connection as con:
            con.execute("UPDATE conversations SET sealed_title=? WHERE id=?", (sealed, conv_id))

    def archive_conversation(
        self, site_id: str, user_id: int, conv_id: str, *, now: int | None = None
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        with self.connection as con:
            con.execute("UPDATE conversations SET archived_at=? WHERE id=?", (_now(now), conv_id))

    def save_citations(
        self, site_id: str, user_id: int, conv_id: str, refs: list[dict[str, Any]]
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        sealed = self._seal_json(
            self.chat_dek(site_id, user_id), refs, bound_to=aad("conversations", conv_id, "cites")
        )
        with self.connection as con:
            con.execute("UPDATE conversations SET sealed_citations=? WHERE id=?", (sealed, conv_id))

    def citations(self, site_id: str, user_id: int, conv_id: str) -> list[dict[str, Any]]:
        row = self._conversation_row(site_id, user_id, conv_id)
        if row["sealed_citations"] is None:
            return []
        refs: list[dict[str, Any]] = self._open_json(
            self.chat_dek(site_id, user_id),
            row["sealed_citations"],
            bound_to=aad("conversations", conv_id, "cites"),
        )
        return refs

    def append_event(
        self,
        site_id: str,
        user_id: int,
        conv_id: str,
        turn_id: str,
        event: dict[str, Any],
        *,
        now: int | None = None,
    ) -> int:
        """Persist an event; returns its per-conversation sequence number."""
        stamp = _now(now)
        self._conversation_row(site_id, user_id, conv_id)
        con = self.connection
        with con:
            seq = int(
                con.execute(
                    "SELECT coalesce(max(seq), 0) + 1 FROM turn_events WHERE conversation_id=?",
                    (conv_id,),
                ).fetchone()[0]
            )
            payload = dict(event, seq=seq)
            con.execute(
                "INSERT INTO turn_events (conversation_id, seq, turn_id, sealed, created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    conv_id,
                    seq,
                    turn_id,
                    self._seal_json(
                        self.chat_dek(site_id, user_id),
                        payload,
                        bound_to=aad("turn_events", conv_id, seq),
                    ),
                    stamp,
                ),
            )
            con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, conv_id))
        return seq

    def events(
        self, site_id: str, user_id: int, conv_id: str, *, after_seq: int = 0
    ) -> list[tuple[int, dict[str, Any]]]:
        self._conversation_row(site_id, user_id, conv_id)
        dek = self.chat_dek(site_id, user_id)
        rows = self.connection.execute(
            "SELECT seq, sealed FROM turn_events WHERE conversation_id=? AND seq>? ORDER BY seq",
            (conv_id, after_seq),
        ).fetchall()
        return [
            (
                r["seq"],
                self._open_json(dek, r["sealed"], bound_to=aad("turn_events", conv_id, r["seq"])),
            )
            for r in rows
        ]

    def start_turn(
        self,
        site_id: str,
        user_id: int,
        conv_id: str,
        turn_id: str,
        *,
        client_msg_id: str | None = None,
        now: int | None = None,
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        with self.connection as con:
            con.execute(
                "INSERT INTO turns (turn_id, conversation_id, client_msg_id, state, created_at) "
                "VALUES (?,?,?,'running',?)",
                (turn_id, conv_id, client_msg_id, _now(now)),
            )

    def turn_for_client_message(self, conv_id: str, client_msg_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT turn_id FROM turns WHERE conversation_id=? AND client_msg_id=?",
            (conv_id, client_msg_id),
        ).fetchone()
        return str(row[0]) if row else None

    def running_turn(self, conv_id: str) -> str | None:
        """The turn that blocks a new one: running, or waiting for the user's answer."""
        row = self.connection.execute(
            "SELECT turn_id FROM turns WHERE conversation_id=? AND state IN ('running','waiting') "
            "ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        return str(row[0]) if row else None

    def turn(self, conv_id: str, turn_id: str) -> Turn:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE turn_id=? AND conversation_id=?", (turn_id, conv_id)
        ).fetchone()
        if row is None:
            raise StoreError("no such turn")
        return Turn(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            state=row["state"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )

    def set_turn_state(self, turn_id: str, state: str, *, now: int | None = None) -> None:
        finished = _now(now) if state in ("done", "failed", "cancelled", "interrupted") else None
        with self.connection as con:
            con.execute(
                "UPDATE turns SET state=?, finished_at=? WHERE turn_id=?",
                (state, finished, turn_id),
            )

    def interrupt_running_turns(self) -> list[tuple[str, int, str, str]]:
        """Crash recovery: turns that were running when the process died."""
        rows = self.connection.execute(
            "SELECT t.turn_id, c.id, c.site_id, c.user_id FROM turns t "
            "JOIN conversations c ON c.id = t.conversation_id WHERE t.state='running'"
        ).fetchall()
        for r in rows:
            self.set_turn_state(r["turn_id"], "interrupted")
        return [(r["site_id"], r["user_id"], r["id"], r["turn_id"]) for r in rows]

    # ------------------------------------------------------------------ #
    # Usage metering and quota settings
    # ------------------------------------------------------------------ #

    def record_usage(
        self,
        site_id: str,
        *,
        user_id: int | None,
        conversation_id: str | None,
        turn_id: str | None,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        estimated: bool,
        ts: int,
    ) -> None:
        with self.connection as con:
            con.execute(
                "INSERT INTO usage (site_id, user_id, conversation_id, turn_id, purpose, "
                "input_tokens, output_tokens, reasoning_tokens, estimated, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    site_id,
                    user_id,
                    conversation_id,
                    turn_id,
                    purpose,
                    input_tokens,
                    output_tokens,
                    reasoning_tokens,
                    int(estimated),
                    ts,
                ),
            )

    def usage_rows(
        self,
        site_id: str,
        *,
        since: int,
        until: int,
        purposes: Collection[str],
        user_id: int | None = None,
    ) -> list[Any]:
        sql = (
            "SELECT user_id, input_tokens, output_tokens, reasoning_tokens FROM usage "
            f"WHERE site_id=? AND ts>=? AND ts<? AND purpose IN ({_marks(purposes)})"
        )
        params: list[Any] = [site_id, since, until, *purposes]
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        return list(self.connection.execute(sql, params).fetchall())

    def _site_json(self, site_id: str, column: str) -> dict[str, Any]:
        row = self._site_row(site_id)
        raw = row[column]
        if not raw:
            return {}
        value: dict[str, Any] = self._open_json(
            self._site_dek(site_id), raw, bound_to=aad("sites", site_id, column)
        )
        return value

    def _set_site_json(self, site_id: str, column: str, value: dict[str, Any]) -> None:
        self._site_row(site_id)
        sealed = self._seal_json(
            self._site_dek(site_id), value, bound_to=aad("sites", site_id, column)
        )
        with self.connection as con:
            con.execute(f"UPDATE sites SET {column}=? WHERE site_id=?", (sealed, site_id))

    def site_policy(self, site_id: str) -> dict[str, Any]:
        return self._site_json(site_id, "policy")

    def set_site_policy(self, site_id: str, policy: dict[str, Any]) -> None:
        self._set_site_json(site_id, "policy", policy)

    def site_ceilings(self, site_id: str) -> dict[str, Any]:
        return self._site_json(site_id, "ceilings")

    def set_site_ceilings(self, site_id: str, ceilings: dict[str, Any]) -> None:
        self._set_site_json(site_id, "ceilings", ceilings)

    # ------------------------------------------------------------------ #
    # LangGraph checkpoint rows (sealed by agent.checkpoint)
    # ------------------------------------------------------------------ #

    def ckpt_rows(self, thread_id: str) -> tuple[list[Any], list[Any], list[Any]]:
        con = self.connection
        return (
            con.execute("SELECT * FROM ckpt_storage WHERE thread_id=?", (thread_id,)).fetchall(),
            con.execute("SELECT * FROM ckpt_writes WHERE thread_id=?", (thread_id,)).fetchall(),
            con.execute("SELECT * FROM ckpt_blobs WHERE thread_id=?", (thread_id,)).fetchall(),
        )

    def ckpt_put(
        self,
        *,
        storage: Sequence[tuple[str, str, str, bytes]] = (),
        writes: Sequence[tuple[str, str, str, str, int, bytes]] = (),
        blobs: Sequence[tuple[str, str, str, str, bytes]] = (),
    ) -> None:
        with self.connection as con:
            con.executemany("INSERT OR REPLACE INTO ckpt_storage VALUES (?,?,?,?)", storage)
            con.executemany("INSERT OR REPLACE INTO ckpt_writes VALUES (?,?,?,?,?,?)", writes)
            con.executemany("INSERT OR REPLACE INTO ckpt_blobs VALUES (?,?,?,?,?)", blobs)

    def ckpt_delete(self, thread_id: str) -> None:
        with self.connection as con:
            for table in ("ckpt_storage", "ckpt_writes", "ckpt_blobs"):
                con.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self, site_id: str) -> dict[str, Any]:
        con = self.connection
        docs = con.execute(
            "SELECT count(*), sum(extract_version IS NULL), sum(skip_reason IS NOT NULL) "
            "FROM documents WHERE site_id=? AND tombstoned_at IS NULL",
            (site_id,),
        ).fetchone()
        last = con.execute(
            "SELECT sync_id, mode, state, started_at, finished_at FROM sync_runs "
            "WHERE site_id=? ORDER BY started_at DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        return {
            "courses": int(
                con.execute(
                    "SELECT count(*) FROM courses WHERE site_id=? AND tombstoned_at IS NULL",
                    (site_id,),
                ).fetchone()[0]
            ),
            "documents": int(docs[0] or 0),
            "pending_extraction": int(docs[1] or 0),
            "skipped": int(docs[2] or 0),
            "chunks": self.chunk_count(site_id),
            "last_sync": dict(last) if last else None,
        }
