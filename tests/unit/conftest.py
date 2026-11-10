"""Unit-test fixtures shared across modules."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import EMBED_DIM

from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.store import Store


@pytest.fixture
def ring() -> KeyRing:
    return KeyRing(os.urandom(32))


@pytest.fixture
def store(tmp_path: Path, ring: KeyRing) -> Iterator[Store]:
    with Store(tmp_path / "index.db", ring, embed_dim=EMBED_DIM) as s:
        yield s
