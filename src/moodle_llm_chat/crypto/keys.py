"""Master key, derived purpose keys, and wrapped data-encryption keys (DEKs).

Hierarchy::

    master key (32 bytes, env or 0600 file, never stored by us)
      ├─ HKDF "db"           → SQLCipher raw key (whole-database encryption)
      ├─ HKDF "kek:content"  → wraps per-course content DEKs and per-blob DEKs
      └─ HKDF "kek:chat"     → wraps per-user chat DEKs

DEKs are random, stored only wrapped (AES key wrap, RFC 3394) in the ``keys`` table,
and cached unwrapped in process memory only. Rotating the master key re-wraps DEKs
(cheap: one operation per DEK); no payload has to be re-encrypted. Deleting a DEK
crypto-shreds everything encrypted under it.
"""

from __future__ import annotations

import base64
import binascii
import os
import stat
from collections import OrderedDict
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

from moodle_llm_chat.config import SecuritySection, env_name

KEY_BYTES = 32
_SALT = b"moodle-llm-chat/v1"
_DEK_CACHE_SIZE = 2048


class MasterKeyError(RuntimeError):
    """The master key is missing, malformed, badly protected, or wrong."""


class Scope(StrEnum):
    CONTENT = "content"
    BLOB = "blob"
    CHAT = "chat"


#: Which KEK wraps each scope's DEKs.
_KEK_FOR = {Scope.CONTENT: "content", Scope.BLOB: "content", Scope.CHAT: "chat"}


class KeyStore(Protocol):
    def wrapped_key(self, scope: str, scope_id: str) -> bytes | None: ...

    def store_wrapped_key(self, scope: str, scope_id: str, wrapped: bytes) -> None: ...

    def delete_wrapped_key(self, scope: str, scope_id: str) -> None: ...

    def all_wrapped_keys(self) -> list[tuple[str, str, bytes]]: ...


def generate_master_key() -> str:
    """A fresh random master key, base64-encoded for an env var or key file."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def _decode(raw: str, source: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyError(f"{source} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise MasterKeyError(f"{source} must decode to {KEY_BYTES} bytes, got {len(key)}")
    return key


def load_master_key(security: SecuritySection) -> bytes:
    """Load the master key, preferring the key file over the env var."""
    if security.master_key_file is not None:
        path = Path(security.master_key_file)
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise MasterKeyError(f"cannot read master key file {path}: {exc}") from exc
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise MasterKeyError(
                f"master key file {path} is accessible by other users; chmod it to 0600"
            )
        return _decode(path.read_text(encoding="ascii"), f"master key file {path}")
    if security.master_key is not None:
        return _decode(security.master_key.get_secret_value(), env_name("security", "master_key"))
    raise MasterKeyError(
        "no master key configured. Set "
        f"{env_name('security', 'master_key_file')} (preferred) or "
        f"{env_name('security', 'master_key')}; generate one with "
        "`moodle-llm-chat gen-master-key`."
    )


def _hkdf(master: bytes, info: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=_SALT, info=info.encode()).derive(
        master
    )


class KeyRing:
    def __init__(self, master: bytes) -> None:
        if len(master) != KEY_BYTES:
            raise MasterKeyError(f"master key must be {KEY_BYTES} bytes")
        self._master = master
        self._keks = {name: _hkdf(master, f"kek:{name}") for name in ("content", "chat")}
        self._deks: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    def database_key(self) -> bytes:
        return _hkdf(self._master, "db")

    def sqlcipher_pragma_value(self) -> str:
        """SQLCipher raw-key syntax: skips its PBKDF2 (our key is already uniform)."""
        return f"x'{self.database_key().hex()}'"

    def kek(self, scope: Scope) -> bytes:
        return self._keks[_KEK_FOR[scope]]

    def derive(self, purpose: str) -> bytes:
        """Another purpose-specific key (e.g. for keyed hashes); never reused."""
        return _hkdf(self._master, f"purpose:{purpose}")

    def dek(self, store: KeyStore, scope: Scope, scope_id: str) -> bytes:
        """The DEK for ``(scope, scope_id)``, created on first use."""
        cache_key = (scope.value, scope_id)
        cached = self._deks.get(cache_key)
        if cached is not None:
            self._deks.move_to_end(cache_key)
            return cached
        wrapped = store.wrapped_key(scope.value, scope_id)
        if wrapped is None:
            dek = os.urandom(KEY_BYTES)
            store.store_wrapped_key(scope.value, scope_id, aes_key_wrap(self.kek(scope), dek))
        else:
            dek = self._unwrap(scope, wrapped)
        self._remember(cache_key, dek)
        return dek

    def forget(self, store: KeyStore, scope: Scope, scope_id: str) -> None:
        """Delete a DEK — everything encrypted under it becomes unrecoverable."""
        store.delete_wrapped_key(scope.value, scope_id)
        self._deks.pop((scope.value, scope_id), None)

    def rewrap_all(self, store: KeyStore, *, previous: KeyRing) -> int:
        """Re-wrap every stored DEK from ``previous``'s KEKs to this ring's."""
        count = 0
        for scope_name, scope_id, wrapped in store.all_wrapped_keys():
            scope = Scope(scope_name)
            dek = previous._unwrap(scope, wrapped)
            store.store_wrapped_key(scope_name, scope_id, aes_key_wrap(self.kek(scope), dek))
            count += 1
        return count

    def _unwrap(self, scope: Scope, wrapped: bytes) -> bytes:
        try:
            return aes_key_unwrap(self.kek(scope), wrapped)
        except InvalidUnwrap as exc:
            raise MasterKeyError(
                "a stored data key cannot be unwrapped — wrong master key?"
            ) from exc

    def _remember(self, key: tuple[str, str], dek: bytes) -> None:
        self._deks[key] = dek
        if len(self._deks) > _DEK_CACHE_SIZE:
            self._deks.popitem(last=False)
