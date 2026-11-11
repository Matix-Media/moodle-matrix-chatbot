"""Encrypted SQLite connections (SQLCipher + sqlite-vec + FTS5)."""

from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path

import pytest

from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.db import DatabaseKeyError, open_encrypted

SECRET = "geheimes-wort-im-index"


def _read_all(path: Path) -> bytes:
    data = b""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            data += p.read_bytes()
    return data


class TestOpenEncrypted:
    def test_file_on_disk_contains_no_plaintext(self, tmp_path: Path) -> None:
        path = tmp_path / "index.db"
        con = open_encrypted(path, KeyRing(os.urandom(32)))
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (SECRET,))
        con.commit()
        con.close()
        raw = _read_all(path)
        assert SECRET.encode() not in raw
        assert not raw.startswith(b"SQLite format 3")

    def test_reopen_with_same_key(self, tmp_path: Path) -> None:
        path = tmp_path / "index.db"
        ring = KeyRing(os.urandom(32))
        con = open_encrypted(path, ring)
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('x')")
        con.commit()
        con.close()
        again = open_encrypted(path, ring)
        assert again.execute("SELECT v FROM t").fetchone()[0] == "x"
        again.close()

    def test_wrong_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "index.db"
        open_encrypted(path, KeyRing(os.urandom(32))).close()
        with pytest.raises(DatabaseKeyError):
            open_encrypted(path, KeyRing(os.urandom(32)))

    def test_unencrypted_legacy_database_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "index.db"
        plain = sqlite3.connect(path)
        plain.execute("CREATE TABLE t (v TEXT)")
        plain.commit()
        plain.close()
        with pytest.raises(DatabaseKeyError):
            open_encrypted(path, KeyRing(os.urandom(32)))

    def test_sqlite_vec_and_fts5_work_inside_the_cipher(self, tmp_path: Path) -> None:
        con = open_encrypted(tmp_path / "index.db", KeyRing(os.urandom(32)))
        con.execute("CREATE VIRTUAL TABLE v USING vec0(id INTEGER PRIMARY KEY, e float[2])")
        con.execute("INSERT INTO v VALUES (1, ?)", (struct.pack("2f", 1.0, 0.0),))
        hit = con.execute(
            "SELECT id FROM v WHERE e MATCH ? AND k = 1", (struct.pack("2f", 1.0, 0.0),)
        ).fetchone()
        assert hit[0] == 1
        con.execute(
            "CREATE VIRTUAL TABLE f USING fts5(text, tokenize='unicode61 remove_diacritics 2')"
        )
        con.execute("INSERT INTO f VALUES ('Prüfung')")
        assert con.execute("SELECT count(*) FROM f WHERE f MATCH 'prufung'").fetchone()[0] == 1
        con.close()

    def test_rows_are_accessible_by_name_and_foreign_keys_enforced(self, tmp_path: Path) -> None:
        con = open_encrypted(tmp_path / "index.db", KeyRing(os.urandom(32)))
        assert con.execute("SELECT 1 AS one").fetchone()["one"] == 1
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        con.close()
