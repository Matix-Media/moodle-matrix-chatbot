"""CLI entry points."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from moodle_llm_chat.cli import app

runner = CliRunner()


class TestDoctor:
    def test_reports_unconfigured_subsystems_without_failing(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "chat model" in result.output
        assert "not configured" in result.output
        assert "MLC_LLM__PROVIDER" in result.output

    def test_reports_configured_provider_and_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_LLM__PROVIDER", "ollama")
        monkeypatch.setenv("MLC_LLM__MODEL", "qwen3")
        monkeypatch.setenv("MLC_LLM__THINKING", "medium")
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "ollama" in result.output
        assert "qwen3" in result.output
        assert "medium" in result.output

    def test_never_prints_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_LLM__PROVIDER", "gemini")
        monkeypatch.setenv("MLC_LLM__MODEL", "gemini-x")
        monkeypatch.setenv("MLC_LLM__API_KEY", "very-secret-value")
        result = runner.invoke(app, ["doctor"])
        assert "very-secret-value" not in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.output


def test_gen_master_key_prints_a_valid_key() -> None:
    import base64

    result = runner.invoke(app, ["gen-master-key"])
    assert result.exit_code == 0
    assert len(base64.b64decode(result.output.strip())) == 32


class TestSiteCommands:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import base64
        import os

        monkeypatch.setenv("MLC_SECURITY__MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
        monkeypatch.setenv("MLC_EMBEDDING__DIM", "3")

    def test_add_prints_secret_once_and_list_never_does(self) -> None:
        added = runner.invoke(
            app, ["site", "add", "schule-a", "--name", "Schule A", "--url", "https://moodle.a.test"]
        )
        assert added.exit_code == 0, added.output
        secret = next(w for w in added.output.split() if w.startswith("mlc_"))
        listed = runner.invoke(app, ["site", "list"])
        assert listed.exit_code == 0
        assert "schule-a" in listed.output
        assert secret not in listed.output

    def test_rotate_prints_new_secret(self) -> None:
        runner.invoke(app, ["site", "add", "s", "--name", "S", "--url", "https://m.test"])
        result = runner.invoke(app, ["site", "rotate", "s", "--grace", "60"])
        assert result.exit_code == 0, result.output
        assert "mlc_" in result.output

    def test_revoke(self) -> None:
        runner.invoke(app, ["site", "add", "s", "--name", "S", "--url", "https://m.test"])
        assert runner.invoke(app, ["site", "revoke", "s"]).exit_code == 0
        assert "revoked" in runner.invoke(app, ["site", "list"]).output

    def test_unknown_site_fails_cleanly(self) -> None:
        result = runner.invoke(app, ["site", "revoke", "missing"])
        assert result.exit_code == 1
        assert "no such site" in result.output

    def test_missing_master_key_fails_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLC_SECURITY__MASTER_KEY")
        result = runner.invoke(app, ["site", "list"])
        assert result.exit_code == 1
        assert "master key" in result.output
