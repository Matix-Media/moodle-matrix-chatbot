"""Regression: ``_write_env`` must be able to target a path other than ``.env``.

``serve`` persists rotated Matrix tokens to a file inside the data volume rather
than ``.env`` — a container's working directory is ephemeral, and even a durable
``.env`` write would lose to the real env var pydantic-settings already sees
(fixed separately in ``Settings.settings_customise_sources``; this test only
covers the writer itself).
"""

from __future__ import annotations

from pathlib import Path

from bsbot.cli import _write_env


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


def test_defaults_to_dotenv_when_no_path_given(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_env({"X": "1"})
    assert (tmp_path / ".env").exists()
