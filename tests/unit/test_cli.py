"""CLI behaviour that matters when things are misconfigured.

`doctor` is the command you reach for when the configuration is broken, so it is
exactly the command that must never answer with a traceback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bsbot.cli import app

runner = CliRunner()


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".env"


def test_doctor_reports_a_malformed_url_instead_of_crashing(env_file: Path) -> None:
    """Regression: a homeserver without a scheme raised a pydantic ValidationError
    traceback, burying the one line the user needed."""
    env_file.write_text(
        "BSBOT_MATRIX__HOMESERVER=matrix.org\n"
        "BSBOT_MATRIX__USER_ID=@bot:matrix.org\n"
        "BSBOT_MATRIX__PASSWORD=pw\n"
    )
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "BSBOT_MATRIX__HOMESERVER" in result.output
    assert "https://" in result.output


def test_doctor_lists_configured_subsystems(env_file: Path) -> None:
    env_file.write_text(
        "BSBOT_MOODLE__BASE_URL=https://moodle.example.de\nBSBOT_MOODLE__TOKEN=abc\n"
    )
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "moodle" in result.output
    assert "gemini" in result.output


def test_doctor_on_empty_config_still_succeeds(env_file: Path) -> None:
    env_file.write_text("")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not configured" in result.output


def test_bench_rejects_a_malformed_as_of_date(env_file: Path) -> None:
    """specs/012-benchmarks.md AC-16: fails fast on a bad --as-of, before touching
    Gemini config at all, so this needs no credentials to exercise."""
    env_file.write_text("")
    result = runner.invoke(
        app, ["bench", "--golden", "config/golden_questions.example.yaml", "--as-of", "2026-13-40"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "--as-of" in result.output
