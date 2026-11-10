"""Typed configuration.

* Settings load permissively: an unconfigured LLM must not stop e.g. ``doctor`` from
  running, so every provider field is optional at load time.
* Validation happens at *use* time via the ``require_*`` accessors, which return
  frozen, non-optional config objects and raise :class:`ConfigError` naming the exact
  environment variables to set.
* Secrets are :class:`~pydantic.SecretStr` so they cannot leak into logs or reprs.

Environment variables use the ``MLC_`` prefix and ``__`` as the nested delimiter,
e.g. ``MLC_LLM__PROVIDER=ollama``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "MLC_"


class ConfigError(RuntimeError):
    """A subsystem was used before it was configured."""


class Provider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"


class ThinkingLevel(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ShowThinking(StrEnum):
    FULL = "full"
    INDICATOR = "indicator"
    OFF = "off"


#: Providers that offer an embeddings endpoint.
EMBEDDING_PROVIDERS = frozenset({Provider.GEMINI, Provider.OPENAI, Provider.OLLAMA})


def env_name(section: str, field: str) -> str:
    return f"{ENV_PREFIX}{section.upper()}__{field.upper()}"


def _normalise_url(value: str | None) -> str | None:
    """Strip whitespace and trailing slashes; require an absolute http(s) URL."""
    if value is None:
        return None
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"must be an absolute http(s) URL, got {value!r}")
    return cleaned


def _needs_api_key(provider: Provider, base_url: str | None) -> bool:
    if provider is Provider.OLLAMA:
        return False
    # An OpenAI-compatible local server (vLLM, LM Studio, llama.cpp) usually has no key.
    return not (provider is Provider.OPENAI and base_url)


# --------------------------------------------------------------------------- #
# Raw (permissive) sections
# --------------------------------------------------------------------------- #


class LLMSection(BaseModel):
    provider: Provider | None = None
    model: str | None = None
    #: Cheap model for titles and OCR. Defaults to ``model``.
    utility_model: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tool_calls: int = Field(default=25, gt=0)
    thinking: ThinkingLevel = ThinkingLevel.OFF
    thinking_budget_tokens: int | None = Field(default=None, gt=0)
    #: Raw provider kwargs merged over the derived thinking params (exotic servers).
    thinking_params: dict[str, Any] | None = None

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _normalise_url(value)


class EmbeddingSection(BaseModel):
    provider: Provider | None = None
    model: str | None = None
    dim: int = Field(default=768, gt=0)
    api_key: SecretStr | None = None
    base_url: str | None = None
    batch_size: int = Field(default=100, gt=0)
    rpm: int = Field(default=90, gt=0)
    items_per_minute: int = Field(default=2500, gt=0)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _normalise_url(value)


class OCRSection(BaseModel):
    enabled: bool = False
    #: Defaults to the LLM section's provider/model when unset.
    model: str | None = None
    text_threshold_chars: int = 80


class MoodleSection(BaseModel):
    callback_timeout_s: float = 5.0
    access_cache_ttl_s: int = 60
    circuit_breaker_failures: int = 3
    circuit_breaker_reset_s: float = 30.0


class SecuritySection(BaseModel):
    master_key: SecretStr | None = None
    master_key_file: Path | None = None
    require_tls: bool = True
    jwt_leeway_s: int = 30
    secret_rotation_grace_s: int = 3600


class ServerSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    max_ws_per_user: int = 3
    burst_per_minute: int = 20


class AgentSection(BaseModel):
    max_steps: int = Field(default=12, gt=0)
    read_max_chars: int = Field(default=6000, gt=0)
    search_default_k: int = Field(default=8, gt=0)
    timezone: str = "Europe/Berlin"
    show_thinking: ShowThinking = ShowThinking.INDICATOR
    disabled_tools: list[str] = Field(default_factory=list)


class PipelineSection(BaseModel):
    fetch_concurrency: int = Field(default=4, gt=0)
    extract_workers: int = Field(default=2, gt=0)
    ocr_concurrency: int = Field(default=2, gt=0)
    per_host_rate: float = Field(default=4.0, gt=0)
    max_file_bytes: int = 25 * 1024 * 1024


class QuotaSection(BaseModel):
    min_headroom_tokens: int = Field(default=2000, gt=0)
    default_max_tokens_per_turn: int = Field(default=60_000, gt=0)
    estimate_when_missing: bool = True


# --------------------------------------------------------------------------- #
# Resolved (validated, non-optional) config
# --------------------------------------------------------------------------- #


class LLMConfig(BaseModel):
    model_config = {"frozen": True}

    provider: Provider
    model: str
    utility_model: str
    api_key: SecretStr | None
    base_url: str | None
    temperature: float
    max_tool_calls: int
    thinking: ThinkingLevel
    thinking_budget_tokens: int | None
    thinking_params: dict[str, Any] | None


class EmbeddingConfig(BaseModel):
    model_config = {"frozen": True}

    provider: Provider
    model: str
    dim: int
    api_key: SecretStr | None
    base_url: str | None
    batch_size: int
    rpm: int
    items_per_minute: int


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSection = Field(default_factory=LLMSection)
    embedding: EmbeddingSection = Field(default_factory=EmbeddingSection)
    ocr: OCRSection = Field(default_factory=OCRSection)
    moodle: MoodleSection = Field(default_factory=MoodleSection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    server: ServerSection = Field(default_factory=ServerSection)
    agent: AgentSection = Field(default_factory=AgentSection)
    pipeline: PipelineSection = Field(default_factory=PipelineSection)
    quota: QuotaSection = Field(default_factory=QuotaSection)

    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        """Resolve eagerly so a later os.chdir cannot move the data."""
        return value.expanduser().resolve()

    # Derived paths — callers must not reassemble these by hand.
    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def index_db(self) -> Path:
        return self.data_dir / "index.db"

    # ----------------------------------------------------------------- #
    # require_* accessors
    # ----------------------------------------------------------------- #

    def require_llm(self) -> LLMConfig:
        s = self.llm
        missing = [env_name("llm", f) for f in ("provider", "model") if not getattr(s, f)]
        if s.provider is not None and not s.api_key and _needs_api_key(s.provider, s.base_url):
            missing.append(env_name("llm", "api_key"))
        if missing:
            raise ConfigError(_missing_message("The chat model", missing))
        assert s.provider is not None and s.model is not None
        return LLMConfig(
            provider=s.provider,
            model=s.model,
            utility_model=s.utility_model or s.model,
            api_key=s.api_key,
            base_url=s.base_url,
            temperature=s.temperature,
            max_tool_calls=s.max_tool_calls,
            thinking=s.thinking,
            thinking_budget_tokens=s.thinking_budget_tokens,
            thinking_params=s.thinking_params,
        )

    def require_embedding(self) -> EmbeddingConfig:
        s = self.embedding
        missing = [env_name("embedding", f) for f in ("provider", "model") if not getattr(s, f)]
        if s.provider is not None and s.provider not in EMBEDDING_PROVIDERS:
            raise ConfigError(
                f"{s.provider.value} has no embeddings API; set "
                f"{env_name('embedding', 'provider')} to one of "
                f"{', '.join(sorted(p.value for p in EMBEDDING_PROVIDERS))}."
            )
        if s.provider is not None and not s.api_key and _needs_api_key(s.provider, s.base_url):
            missing.append(env_name("embedding", "api_key"))
        if missing:
            raise ConfigError(_missing_message("The embedding model", missing))
        assert s.provider is not None and s.model is not None
        return EmbeddingConfig(
            provider=s.provider,
            model=s.model,
            dim=s.dim,
            api_key=s.api_key,
            base_url=s.base_url,
            batch_size=s.batch_size,
            rpm=s.rpm,
            items_per_minute=s.items_per_minute,
        )


def _missing_message(subsystem: str, missing: list[str]) -> str:
    joined = "\n  - ".join(missing)
    return (
        f"{subsystem} is not configured.\n"
        f"Set the following in your environment or .env file:\n  - {joined}\n"
        f"See .env.example for the full list."
    )


def load_settings() -> Settings:
    """Load settings from the environment and .env."""
    return Settings()
