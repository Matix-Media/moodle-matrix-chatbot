"""AES-256-GCM sealing for column values and streamed blob files.

Column format: ``0x01 || nonce(12) || ciphertext+tag``. The AAD binds a ciphertext to
the exact row and column it belongs to (e.g. ``b"chunks|<doc_id>|<ordinal>|text"``),
so a value copied into another row fails to decrypt instead of being accepted.

Blob format: ``b"MLCBv1"`` header, then frames of
``len(4, big-endian) || nonce(12) || ciphertext+tag`` where ``len`` covers the
ciphertext+tag only. Each frame's AAD is ``aad || index(8) || final(1)``, so frames
cannot be reordered, dropped from the middle, or truncated at the end undetected.
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO, Literal, overload

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"
NONCE_BYTES = 12
TAG_BYTES = 16
BLOB_MAGIC = b"MLCBv1"
DEFAULT_FRAME = 1024 * 1024


class DecryptionError(RuntimeError):
    """Ciphertext is corrupt, was moved, or the key is wrong."""


def aad(*parts: str | int) -> bytes:
    """Build an AAD from row-identifying parts."""
    return "|".join(str(p) for p in parts).encode("utf-8")


def seal(dek: bytes, value: str | bytes, *, aad: bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    nonce = os.urandom(NONCE_BYTES)
    return VERSION + nonce + AESGCM(dek).encrypt(nonce, data, aad)


@overload
def open_box(dek: bytes, box: bytes, *, aad: bytes, text: Literal[True] = ...) -> str: ...
@overload
def open_box(dek: bytes, box: bytes, *, aad: bytes, text: Literal[False]) -> bytes: ...
def open_box(dek: bytes, box: bytes, *, aad: bytes, text: bool = True) -> str | bytes:
    if len(box) < 1 + NONCE_BYTES + TAG_BYTES or box[:1] != VERSION:
        raise DecryptionError("not a sealed value")
    nonce = box[1 : 1 + NONCE_BYTES]
    try:
        data = AESGCM(dek).decrypt(nonce, box[1 + NONCE_BYTES :], aad)
    except InvalidTag as exc:
        raise DecryptionError("authentication failed") from exc
    return data.decode("utf-8") if text else data


def _frame_aad(base: bytes, index: int, final: bool) -> bytes:
    return base + struct.pack(">QB", index, 1 if final else 0)


class StreamEncryptor:
    """Incremental blob encryption: feed bytes as they arrive, then :meth:`close`.

    A frame is only emitted once more data is known to follow it, so the last frame
    can be flagged final without a look-ahead read.
    """

    def __init__(
        self, dest: BinaryIO, dek: bytes, *, aad: bytes, frame_size: int = DEFAULT_FRAME
    ) -> None:
        self._dest = dest
        self._gcm = AESGCM(dek)
        self._aad = aad
        self._frame = frame_size
        self._buf = bytearray()
        self._index = 0
        self.plaintext_bytes = 0
        self._closed = False
        dest.write(BLOB_MAGIC)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("encryptor is closed")
        self._buf += data
        self.plaintext_bytes += len(data)
        while len(self._buf) > self._frame:
            self._emit(bytes(self._buf[: self._frame]), final=False)
            del self._buf[: self._frame]

    def close(self) -> None:
        if not self._closed:
            self._emit(bytes(self._buf), final=True)
            self._buf.clear()
            self._closed = True

    def _emit(self, chunk: bytes, *, final: bool) -> None:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._gcm.encrypt(nonce, chunk, _frame_aad(self._aad, self._index, final))
        self._dest.write(struct.pack(">I", len(sealed)) + nonce + sealed)
        self._index += 1


def encrypt_stream(
    source: BinaryIO,
    dest: BinaryIO,
    dek: bytes,
    *,
    aad: bytes,
    frame_size: int = DEFAULT_FRAME,
) -> int:
    """Encrypt ``source`` into ``dest`` frame by frame; returns plaintext bytes read."""
    enc = StreamEncryptor(dest, dek, aad=aad, frame_size=frame_size)
    while chunk := source.read(frame_size):
        enc.write(chunk)
    enc.close()
    return enc.plaintext_bytes


def decrypt_stream(source: BinaryIO, dest: BinaryIO, dek: bytes, *, aad: bytes) -> int:
    """Decrypt a blob written by :func:`encrypt_stream`; returns plaintext bytes."""
    if source.read(len(BLOB_MAGIC)) != BLOB_MAGIC:
        raise DecryptionError("not an encrypted blob")
    gcm = AESGCM(dek)
    total = 0
    index = 0
    while True:
        header = source.read(4)
        if not header:
            raise DecryptionError("blob is truncated (no final frame)")
        if len(header) != 4:
            raise DecryptionError("blob is truncated")
        (length,) = struct.unpack(">I", header)
        nonce = source.read(NONCE_BYTES)
        sealed = source.read(length)
        if len(nonce) != NONCE_BYTES or len(sealed) != length:
            raise DecryptionError("blob is truncated")
        # The final flag is authenticated: try "not final" first, then "final".
        for final in (False, True):
            try:
                plain = gcm.decrypt(nonce, sealed, _frame_aad(aad, index, final))
            except InvalidTag:
                continue
            break
        else:
            raise DecryptionError("blob frame failed authentication")
        dest.write(plain)
        total += len(plain)
        if final:
            if source.read(1):
                raise DecryptionError("data after the final frame")
            return total
        index += 1
