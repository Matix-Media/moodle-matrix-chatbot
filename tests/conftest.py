"""Shared fixtures.

The autouse env fixture is important: without it, a developer's real ``MLC_*``
shell variables would leak into unit tests and make them pass or fail depending on
whose machine they run on.

``MLC_DATA_DIR`` is pointed at an empty temp directory for the same reason: tests run
from the project root, which may contain a developer's real ``data/`` (index, keys).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Remove every MLC_* variable and point MLC_DATA_DIR at an empty directory."""
    for key in list(os.environ):
        if key.startswith("MLC_"):
            monkeypatch.delenv(key, raising=False)
    empty_data_dir: Path = tmp_path_factory.mktemp("mlc-data")
    monkeypatch.setenv("MLC_DATA_DIR", str(empty_data_dir))
    yield
