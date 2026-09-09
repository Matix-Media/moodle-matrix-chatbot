"""Regression: ``_write_env`` must always target an explicit path, never ``.env``.

A silent ``.env`` default is exactly what broke ``matrix-login`` run inside a
deployed container — that path is neither writable (non-root user, nothing baked
into the image) nor persistent (only the data volume survives a restart) there.
Every real caller passes ``settings.token_overrides_file`` instead: that path
lives inside the persistent data volume and is loaded with priority over real env
vars (fixed separately in ``Settings.settings_customise_sources``; this test only
covers the writer itself).
"""

from __future__ import annotations

from pathlib import Path

from bsbot.cli._common import write_env as _write_env


def test_writes_to_the_given_path_not_dotenv(tmp_path: Path) -> None:
    target = tmp_path / "data" / "matrix-tokens.env"
    _write_env({"BSBOT_MATRIX__REFRESH_TOKEN": "abc"}, target)
    assert target.read_text().strip() == "BSBOT_MATRIX__REFRESH_TOKEN=abc"
    assert not (tmp_path / ".env").exists()


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "matrix-tokens.env"
    _write_env({"X": "1"}, target)
    assert target.exists()


def test_replaces_existing_key_in_place(tmp_path: Path) -> None:
    target = tmp_path / "matrix-tokens.env"
    target.write_text("BSBOT_MATRIX__REFRESH_TOKEN=old\nOTHER=keep\n")
    _write_env({"BSBOT_MATRIX__REFRESH_TOKEN": "new"}, target)
    lines = target.read_text().splitlines()
    assert "BSBOT_MATRIX__REFRESH_TOKEN=new" in lines
    assert "OTHER=keep" in lines
    assert len(lines) == 2
