"""Configuration: permissive load, strict ``require_*`` accessors."""

from __future__ import annotations

from pathlib import Path

import pytest

from moodle_llm_chat.config import (
    ConfigError,
    Provider,
    Settings,
    ThinkingLevel,
)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


class TestLoading:
    def test_loads_with_nothing_configured(self) -> None:
        settings = Settings()
        assert settings.llm.provider is None
        assert settings.log_level == "INFO"

    def test_env_prefix_is_mlc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, MLC_LOG_LEVEL="DEBUG")
        assert settings.log_level == "DEBUG"

    def test_old_bsbot_prefix_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, BSBOT_LOG_LEVEL="DEBUG")
        assert settings.log_level == "INFO"

    def test_data_dir_is_resolved_and_paths_derive_from_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        settings = _settings(monkeypatch, MLC_DATA_DIR=str(tmp_path / "x" / ".." / "d"))
        assert settings.data_dir == (tmp_path / "d").resolve()
        assert settings.index_db == settings.data_dir / "index.db"
        assert settings.blobs_dir == settings.data_dir / "blobs"

    def test_secrets_never_appear_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            monkeypatch,
            MLC_LLM__PROVIDER="gemini",
            MLC_LLM__MODEL="m",
            MLC_LLM__API_KEY="super-secret-key",
        )
        assert "super-secret-key" not in repr(settings)
        assert "super-secret-key" not in repr(settings.require_llm())


class TestRequireLLM:
    def test_unconfigured_names_the_variables(self) -> None:
        with pytest.raises(ConfigError) as err:
            Settings().require_llm()
        assert "MLC_LLM__PROVIDER" in str(err.value)
        assert "MLC_LLM__MODEL" in str(err.value)

    @pytest.mark.parametrize("provider", ["gemini", "anthropic"])
    def test_hosted_providers_need_a_key(
        self, monkeypatch: pytest.MonkeyPatch, provider: str
    ) -> None:
        settings = _settings(monkeypatch, MLC_LLM__PROVIDER=provider, MLC_LLM__MODEL="m")
        with pytest.raises(ConfigError, match="MLC_LLM__API_KEY"):
            settings.require_llm()

    def test_ollama_needs_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _settings(
            monkeypatch, MLC_LLM__PROVIDER="ollama", MLC_LLM__MODEL="qwen3"
        ).require_llm()
        assert cfg.provider is Provider.OLLAMA
        assert cfg.api_key is None

    def test_openai_without_base_url_needs_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch, MLC_LLM__PROVIDER="openai", MLC_LLM__MODEL="gpt")
        with pytest.raises(ConfigError, match="MLC_LLM__API_KEY"):
            settings.require_llm()

    def test_openai_compatible_local_server_needs_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _settings(
            monkeypatch,
            MLC_LLM__PROVIDER="openai",
            MLC_LLM__MODEL="local",
            MLC_LLM__BASE_URL="http://localhost:1234/v1/",
        ).require_llm()
        assert cfg.base_url == "http://localhost:1234/v1"

    def test_unknown_provider_rejected_at_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_LLM__PROVIDER", "nope")
        with pytest.raises(ValueError):
            Settings()

    def test_utility_model_defaults_to_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _settings(
            monkeypatch, MLC_LLM__PROVIDER="ollama", MLC_LLM__MODEL="qwen3"
        ).require_llm()
        assert cfg.utility_model == "qwen3"

    def test_thinking_defaults_off_and_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        base = {"MLC_LLM__PROVIDER": "ollama", "MLC_LLM__MODEL": "qwen3"}
        assert _settings(monkeypatch, **base).require_llm().thinking is ThinkingLevel.OFF
        cfg = _settings(monkeypatch, **base, MLC_LLM__THINKING="high").require_llm()
        assert cfg.thinking is ThinkingLevel.HIGH

    def test_thinking_params_is_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _settings(
            monkeypatch,
            MLC_LLM__PROVIDER="ollama",
            MLC_LLM__MODEL="qwen3",
            MLC_LLM__THINKING_PARAMS='{"think": "high"}',
        ).require_llm()
        assert cfg.thinking_params == {"think": "high"}


class TestRequireEmbedding:
    def test_anthropic_has_no_embeddings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            monkeypatch,
            MLC_EMBEDDING__PROVIDER="anthropic",
            MLC_EMBEDDING__MODEL="x",
            MLC_EMBEDDING__API_KEY="k",
        )
        with pytest.raises(ConfigError, match="anthropic"):
            settings.require_embedding()

    def test_ollama_embedding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _settings(
            monkeypatch,
            MLC_EMBEDDING__PROVIDER="ollama",
            MLC_EMBEDDING__MODEL="nomic-embed-text",
            MLC_EMBEDDING__DIM="768",
        ).require_embedding()
        assert cfg.provider is Provider.OLLAMA
        assert cfg.dim == 768

    def test_dim_must_be_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLC_EMBEDDING__DIM", "0")
        with pytest.raises(ValueError):
            Settings()

    def test_unconfigured_names_variables(self) -> None:
        with pytest.raises(ConfigError, match="MLC_EMBEDDING__PROVIDER"):
            Settings().require_embedding()


class TestDefaults:
    def test_agent_and_pipeline_defaults(self) -> None:
        settings = Settings()
        assert settings.agent.timezone == "Europe/Berlin"
        assert settings.agent.max_steps > 0
        assert settings.pipeline.fetch_concurrency >= 1
        assert settings.security.require_tls is True
        assert settings.quota.min_headroom_tokens > 0
