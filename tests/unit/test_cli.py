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
