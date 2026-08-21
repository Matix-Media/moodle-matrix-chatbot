"""Shared fixtures.

The autouse env fixture is important: without it, a developer's real ``BSBOT_*``
shell variables would leak into unit tests and make them pass or fail depending on
whose machine they run on.

The ``BSBOT_DATA_DIR`` default matters for the same reason but is easy to miss:
``Settings.settings_customise_sources`` reads a token-overrides file from
``BSBOT_DATA_DIR`` (default ``./data``) with priority over real env vars. Tests run
with the project root as their working directory, which really does contain a
``data/`` folder — a locally running bot's actual Matrix refresh token lives there.
Without pointing every test at an empty directory, a test asserting "no credentials
configured" would silently see the developer's real, live token instead.
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
    """Remove every BSBOT_* variable and point BSBOT_DATA_DIR at an empty directory."""
    for key in list(os.environ):
        if key.startswith("BSBOT_"):
            monkeypatch.delenv(key, raising=False)
    empty_data_dir: Path = tmp_path_factory.mktemp("bsbot-data")
    monkeypatch.setenv("BSBOT_DATA_DIR", str(empty_data_dir))
    yield
