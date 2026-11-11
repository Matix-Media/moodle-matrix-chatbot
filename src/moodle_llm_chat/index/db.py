"""Open the SQLCipher-encrypted database.

Whole-file encryption (AES-256 pages + HMAC) covers every table, including the FTS5
token index, sqlite-vec vectors and agent checkpoints, so nothing that touches disk is
readable without the key. Payload columns are additionally sealed per course/user
(see :mod:`moodle_llm_chat.crypto.envelope`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlcipher3
import sqlite_vec

from moodle_llm_chat.crypto.keys import KeyRing

#: The DB-API module in use, so callers can catch its exceptions.
dbapi: Any = sqlcipher3
Connection = sqlcipher3.Connection


class DatabaseKeyError(RuntimeError):
    """The database is not encrypted with this key (or not encrypted at all)."""


def open_encrypted(path: Path, ring: KeyRing, *, check_same_thread: bool = True) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlcipher3.connect(str(path), check_same_thread=check_same_thread)
    con.row_factory = sqlcipher3.Row
    # Raw-key syntax: our key is already uniform, so skip SQLCipher's PBKDF2.
    con.execute(f'PRAGMA key = "{ring.sqlcipher_pragma_value()}"')
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher3.DatabaseError as exc:
        con.close()
        raise DatabaseKeyError(
            f"{path} cannot be opened with this master key. It is either encrypted with "
            "a different key or an unencrypted database from an older version."
        ) from exc

    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)

    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con
