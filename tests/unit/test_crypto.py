"""Encryption at rest: master key handling, key derivation, DEK wrapping, AEAD."""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pytest

from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.envelope import (
    DecryptionError,
    decrypt_stream,
    encrypt_stream,
    open_box,
    seal,
)
from moodle_llm_chat.crypto.keys import (
    KeyRing,
    MasterKeyError,
    Scope,
    generate_master_key,
    load_master_key,
)

KEY = os.urandom(32)


class MemoryKeyStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], bytes] = {}

    def wrapped_key(self, scope: str, scope_id: str) -> bytes | None:
        return self.rows.get((scope, scope_id))

    def store_wrapped_key(self, scope: str, scope_id: str, wrapped: bytes) -> None:
        self.rows[(scope, scope_id)] = wrapped

    def delete_wrapped_key(self, scope: str, scope_id: str) -> None:
        self.rows.pop((scope, scope_id), None)

    def all_wrapped_keys(self) -> list[tuple[str, str, bytes]]:
        return [(s, i, w) for (s, i), w in self.rows.items()]


class TestMasterKey:
    def test_generated_key_is_32_random_bytes_base64(self) -> None:
        a, b = generate_master_key(), generate_master_key()
        assert a != b
        assert len(base64.b64decode(a)) == 32

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY", base64.b64encode(KEY).decode())
        assert load_master_key(Settings().security) == KEY

    def test_from_file_with_strict_permissions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        path = tmp_path / "master.key"
        path.write_text(base64.b64encode(KEY).decode() + "\n")
        path.chmod(0o600)
        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY_FILE", str(path))
        assert load_master_key(Settings().security) == KEY

    def test_world_readable_file_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        path = tmp_path / "master.key"
        path.write_text(base64.b64encode(KEY).decode())
        path.chmod(0o644)
        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY_FILE", str(path))
        with pytest.raises(MasterKeyError, match="0600"):
            load_master_key(Settings().security)

    def test_wrong_length_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY", base64.b64encode(b"short").decode())
        with pytest.raises(MasterKeyError, match="32 bytes"):
            load_master_key(Settings().security)

    def test_missing_key_names_the_variables(self) -> None:
        with pytest.raises(MasterKeyError, match="MLC_SECURITY__MASTER_KEY_FILE"):
            load_master_key(Settings().security)

    def test_key_never_appears_in_settings_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        encoded = base64.b64encode(KEY).decode()
        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY", encoded)
        assert encoded not in repr(Settings())


class TestKeyRing:
    def test_purpose_keys_are_deterministic_and_distinct(self) -> None:
        a, b = KeyRing(KEY), KeyRing(KEY)
        assert a.database_key() == b.database_key()
        assert len({a.database_key(), a.kek(Scope.CONTENT), a.kek(Scope.CHAT)}) == 3
        assert KeyRing(os.urandom(32)).database_key() != a.database_key()

    def test_database_key_is_sqlcipher_raw_hex(self) -> None:
        pragma = KeyRing(KEY).sqlcipher_pragma_value()
        assert pragma.startswith("x'") and pragma.endswith("'")
        assert len(pragma) == 2 + 64 + 1

    def test_dek_is_created_once_and_stored_wrapped(self) -> None:
        store = MemoryKeyStore()
        ring = KeyRing(KEY)
        first = ring.dek(store, Scope.CONTENT, "site1:42")
        again = KeyRing(KEY).dek(store, Scope.CONTENT, "site1:42")
        assert first == again
        assert len(first) == 32
        (wrapped,) = store.rows.values()
        assert first not in wrapped  # never stored in the clear

    def test_deks_differ_per_scope_id_and_scope(self) -> None:
        store = MemoryKeyStore()
        ring = KeyRing(KEY)
        a = ring.dek(store, Scope.CHAT, "site1:7")
        b = ring.dek(store, Scope.CHAT, "site1:8")
        c = ring.dek(store, Scope.CONTENT, "site1:7")
        assert len({a, b, c}) == 3

    def test_other_master_key_cannot_unwrap(self) -> None:
        store = MemoryKeyStore()
        KeyRing(KEY).dek(store, Scope.CHAT, "u")
        with pytest.raises(MasterKeyError):
            KeyRing(os.urandom(32)).dek(store, Scope.CHAT, "u")

    def test_rotation_rewraps_every_dek_without_changing_it(self) -> None:
        store = MemoryKeyStore()
        old = KeyRing(KEY)
        deks = {sid: old.dek(store, Scope.CONTENT, sid) for sid in ("a", "b")}
        new_master = os.urandom(32)
        count = KeyRing(new_master).rewrap_all(store, previous=old)
        assert count == 2
        fresh = KeyRing(new_master)
        for sid, dek in deks.items():
            assert fresh.dek(store, Scope.CONTENT, sid) == dek
        with pytest.raises(MasterKeyError):
            KeyRing(KEY).dek(store, Scope.CONTENT, "a")

    def test_forget_removes_the_key(self) -> None:
        """Crypto-shredding: deleting a DEK makes its ciphertext unrecoverable."""
        store = MemoryKeyStore()
        ring = KeyRing(KEY)
        dek = ring.dek(store, Scope.BLOB, "sha")
        ring.forget(store, Scope.BLOB, "sha")
        assert store.rows == {}
        assert ring.dek(store, Scope.BLOB, "sha") != dek


class TestSeal:
    def test_round_trip(self) -> None:
        dek = os.urandom(32)
        box = seal(dek, "Prüfung am Montag", aad=b"chunks|d1|0|text")
        assert b"Montag" not in box
        assert open_box(dek, box, aad=b"chunks|d1|0|text") == "Prüfung am Montag"

    def test_nonces_are_unique(self) -> None:
        dek = os.urandom(32)
        assert seal(dek, "x", aad=b"a") != seal(dek, "x", aad=b"a")

    def test_aad_binds_ciphertext_to_its_row(self) -> None:
        dek = os.urandom(32)
        box = seal(dek, "secret", aad=b"messages|1|content")
        with pytest.raises(DecryptionError):
            open_box(dek, box, aad=b"messages|2|content")

    def test_wrong_key_fails(self) -> None:
        box = seal(os.urandom(32), "secret", aad=b"a")
        with pytest.raises(DecryptionError):
            open_box(os.urandom(32), box, aad=b"a")

    def test_tampering_fails(self) -> None:
        dek = os.urandom(32)
        box = bytearray(seal(dek, "secret", aad=b"a"))
        box[-1] ^= 1
        with pytest.raises(DecryptionError):
            open_box(dek, bytes(box), aad=b"a")

    def test_bytes_round_trip(self) -> None:
        dek = os.urandom(32)
        assert open_box(dek, seal(dek, b"\x00\x01", aad=b"a"), aad=b"a", text=False) == b"\x00\x01"


class TestStream:
    def _encrypt(self, data: bytes, dek: bytes, *, frame: int = 8) -> bytes:
        out = io.BytesIO()
        encrypt_stream(io.BytesIO(data), out, dek, aad=b"blob|abc", frame_size=frame)
        return out.getvalue()

    def test_round_trip_multiple_frames(self) -> None:
        dek = os.urandom(32)
        data = os.urandom(50)
        blob = self._encrypt(data, dek)
        out = io.BytesIO()
        decrypt_stream(io.BytesIO(blob), out, dek, aad=b"blob|abc")
        assert out.getvalue() == data

    def test_empty_input(self) -> None:
        dek = os.urandom(32)
        out = io.BytesIO()
        decrypt_stream(io.BytesIO(self._encrypt(b"", dek)), out, dek, aad=b"blob|abc")
        assert out.getvalue() == b""

    def test_truncation_is_detected(self) -> None:
        dek = os.urandom(32)
        blob = self._encrypt(os.urandom(50), dek)
        # Drop the final frame (length prefix + nonce + 2 bytes + tag): every
        # remaining frame is individually valid, so only the final flag catches it.
        cut = blob[: len(blob) - (4 + 12 + 2 + 16)]
        with pytest.raises(DecryptionError, match="truncated"):
            decrypt_stream(io.BytesIO(cut), io.BytesIO(), dek, aad=b"blob|abc")

    def test_reordering_is_detected(self) -> None:
        dek = os.urandom(32)
        blob = self._encrypt(os.urandom(16), dek, frame=8)
        header, rest = blob[:6], blob[6:]
        size = 4 + 12 + 8 + 16
        swapped = header + rest[size : 2 * size] + rest[:size] + rest[2 * size :]
        with pytest.raises(DecryptionError):
            decrypt_stream(io.BytesIO(swapped), io.BytesIO(), dek, aad=b"blob|abc")

    def test_wrong_aad_fails(self) -> None:
        dek = os.urandom(32)
        blob = self._encrypt(b"hello world", dek)
        with pytest.raises(DecryptionError):
            decrypt_stream(io.BytesIO(blob), io.BytesIO(), dek, aad=b"blob|other")
