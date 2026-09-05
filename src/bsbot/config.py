"""Typed configuration for every subsystem — see ``specs/001-configuration.md``.

Design notes:

* Settings load permissively. A missing Gemini key must not stop Moodle-only work
  (milestone M1) from running, so every field is optional at load time.
* Validation happens at *use* time via the ``require_*`` accessors, which return
  narrowed, non-optional config objects and raise :class:`ConfigError` naming the
  exact environment variables to set. This keeps ``str | None`` out of the code
  that actually does the work.
* Secrets are :class:`~pydantic.SecretStr` so they cannot leak into logs or
  tracebacks.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import DotEnvSettingsSource

ENV_PREFIX = "BSBOT_"


class ConfigError(RuntimeError):
    """A subsystem was used before it was configured."""


def _env_name(section: str, field: str) -> str:
    return f"{ENV_PREFIX}{section.upper()}__{field.upper()}"


TOKEN_OVERRIDES_FILENAME = "matrix-tokens.env"


def _bootstrap_data_dir() -> Path:
    """Resolve BSBOT_DATA_DIR without constructing a full Settings.

    ``settings_customise_sources`` runs as a classmethod before any instance
    exists, so it cannot read ``self.data_dir``. This mirrors the same default
    and resolution the ``data_dir`` field itself applies.
    """
    import os

    raw = os.environ.get(f"{ENV_PREFIX}DATA_DIR", "./data")
    return Path(raw).expanduser().resolve()


def _normalise_base_url(value: str | None) -> str | None:
    """Strip whitespace and trailing slashes; require an http(s) scheme."""
    if value is None:
        return None
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"must be an absolute http(s) URL such as 'https://moodle.example.de', got {value!r}"
        )
    return cleaned


# --------------------------------------------------------------------------- #
# Raw (permissive) sections
# --------------------------------------------------------------------------- #


class MoodleSection(BaseModel):
    base_url: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    token: SecretStr | None = None
    service: str = "moodle_mobile_app"

    # Tuning knobs. Defaults are deliberately gentle: this points at a school's
    # server, not ours.
    timeout_s: float = 30.0
    max_retries: int = 3
    max_concurrency: int = 4
    retry_backoff_s: float = 0.5

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        return _normalise_base_url(value)


class GeminiSection(BaseModel):
    api_key: SecretStr | None = None
    # Both on flash-lite: the pipeline already compensates for a weaker single
    # model with retrieval quality (hybrid search, expansion, rerank), and the
    # cost difference across every call in a chatty class room adds up fast.
    answer_model: str = "gemini-3.5-flash-lite"
    utility_model: str = "gemini-3.5-flash-lite"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768
    # gemini-embedding-001 accepts at most 250 inputs / 20k tokens per request.
    embed_batch_size: int = 100
    embed_rpm: int = 90  # requests/min
    # Google meters embeddings by ITEMS per minute (measured: 3000 for
    # gemini-embedding-001). This is the limit that actually bites.
    embed_items_per_minute: int = 2500
    # Image ingestion mode: "placeholder" (clean link/alt), "ocr" (text transcription),
    # or "describe" (AI vision semantic explanation)
    image_mode: str = "placeholder"


class PiiSection(BaseModel):
    """PII tokenization (spec 013) — off by default, see AC-23."""

    #: A new, offline NER dependency and an extra local processing pass per
    #: document/message/question. Existing deployments must opt in and then
    #: run a one-time reindex — see ``Store.reset_extraction_for_all_documents``.
    enabled: bool = False
    spacy_model: str = "de_core_news_md"


class MatrixSection(BaseModel):
    homeserver: str | None = None
    user_id: str | None = None
    password: SecretStr | None = None
    #: Preferred over a password. matrix.org increasingly issues accounts via
    #: next-gen auth (MAS/OIDC), where m.login.password is rejected even though the
    #: flow is still advertised. A token also avoids storing a password on disk.
    access_token: SecretStr | None = None
    #: Required alongside a token for E2EE: message keys are bound to a device.
    device_id: str | None = None
    #: Log in with this instead of the MXID. Set it to an e-mail address when the
    #: account's login name is not the MXID localpart.
    login_identifier: str | None = None
    #: OAuth refresh credentials (written by `bsbot matrix-login`). These are what
    #: let the bot survive restarts and token expiry without a human reopening the
    #: browser flow — MAS access tokens live only minutes.
    refresh_token: SecretStr | None = None
    oauth_client_id: str | None = None
    oauth_token_endpoint: str | None = None
    device_name: str = "bsbot"
    # Comma-separated in the environment; split by require_matrix(). Kept as a
    # plain string here because pydantic-settings would otherwise try to JSON-decode it.
    room_ids: str | None = None
    #: AC-27 (specs/009-matrix-bot.md): per-user daily question quota.
    rate_limit_per_day: int = 5
    #: AC-28: user IDs (comma-separated, e.g. teachers) exempt from all rate limits.
    rate_limit_bypass_users: str | None = None

    @field_validator("homeserver")
    @classmethod
    def _check_homeserver(cls, value: str | None) -> str | None:
        return _normalise_base_url(value)


# --------------------------------------------------------------------------- #
# Resolved (validated, non-optional) config
# --------------------------------------------------------------------------- #


class MoodleConfig(BaseModel):
    """Moodle settings, guaranteed usable."""

    model_config = {"frozen": True}

    base_url: str
    service: str
    token: SecretStr | None
    username: str | None
    password: SecretStr | None
    timeout_s: float
    max_retries: int
    max_concurrency: int
    retry_backoff_s: float

    @property
    def token_endpoint(self) -> str:
        return f"{self.base_url}/login/token.php"

    @property
    def rest_endpoint(self) -> str:
        return f"{self.base_url}/webservice/rest/server.php"


class GeminiConfig(BaseModel):
    model_config = {"frozen": True}

    api_key: SecretStr
    answer_model: str
    utility_model: str
    embed_model: str
    embed_dim: int
    embed_batch_size: int
    embed_rpm: int
    embed_items_per_minute: int
    image_mode: str = "placeholder"


class MatrixConfig(BaseModel):
    model_config = {"frozen": True}

    homeserver: str
    user_id: str
    password: SecretStr | None
    access_token: SecretStr | None
    device_id: str | None
    login_identifier: str | None
    refresh_token: SecretStr | None
    oauth_client_id: str | None
    oauth_token_endpoint: str | None
    device_name: str
    room_ids: list[str]
    rate_limit_per_day: int
    rate_limit_bypass_users: list[str]


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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the runtime token-overrides file above real env vars.

        pydantic-settings' default order is init > env vars > .env file > defaults.
        That is wrong for one specific case: MAS rotates the Matrix refresh token on
        every use, and the rotated value is persisted to a file inside the
        persistent data volume (``token_overrides_file``). In a container, a
        Compose ``env_file:`` or a platform's "Environment" panel becomes a real
        process env var — which would otherwise always beat that file, making the
        rotation invisible after every restart and leaving the bot stuck retrying a
        refresh token MAS has already invalidated. The override source is placed
        above ``env_settings`` (but below explicit init kwargs) to fix exactly that,
        without changing precedence for anything else.
        """
        token_overrides = DotEnvSettingsSource(
            settings_cls,
            env_file=_bootstrap_data_dir() / TOKEN_OVERRIDES_FILENAME,
            env_file_encoding="utf-8",
            env_prefix=ENV_PREFIX,
            env_nested_delimiter="__",
        )
        return (init_settings, token_overrides, env_settings, dotenv_settings, file_secret_settings)

    moodle: MoodleSection = Field(default_factory=MoodleSection)
    gemini: GeminiSection = Field(default_factory=GeminiSection)
    matrix: MatrixSection = Field(default_factory=MatrixSection)
    pii: PiiSection = Field(default_factory=PiiSection)

    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    #: Minutes between sync -> index -> embed cycles for `bsbot cron`. School
    #: content changes over days, not minutes, and the unchanged-content skip in
    #: Indexer._index_one makes a no-op cycle cheap but not free — so this stays
    #: moderate (3h) rather than aggressive.
    sync_interval_minutes: int = 180

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        """Resolve eagerly so a later os.chdir cannot move the cache."""
        return value.expanduser().resolve()

    # Derived paths — callers must not reassemble these by hand.
    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def index_db(self) -> Path:
        return self.data_dir / "index.db"

    @property
    def matrix_store_dir(self) -> Path:
        return self.data_dir / "matrix_store"

    @property
    def token_overrides_file(self) -> Path:
        """Where rotated Matrix tokens are persisted — see settings_customise_sources."""
        return self.data_dir / TOKEN_OVERRIDES_FILENAME

    # ----------------------------------------------------------------- #
    # require_* accessors
    # ----------------------------------------------------------------- #

    def require_moodle(self) -> MoodleConfig:
        missing: list[str] = []
        if not self.moodle.base_url:
            missing.append(_env_name("moodle", "base_url"))
        if not self.moodle.token and not (self.moodle.username and self.moodle.password):
            missing.append(
                f"{_env_name('moodle', 'token')} "
                f"(or {_env_name('moodle', 'username')} + {_env_name('moodle', 'password')})"
            )
        if missing:
            raise ConfigError(_missing_message("Moodle", "M1", missing))

        assert self.moodle.base_url is not None  # narrowed by the check above
        return MoodleConfig(
            base_url=self.moodle.base_url,
            service=self.moodle.service,
            token=self.moodle.token,
            username=self.moodle.username,
            password=self.moodle.password,
            timeout_s=self.moodle.timeout_s,
            max_retries=self.moodle.max_retries,
            max_concurrency=self.moodle.max_concurrency,
            retry_backoff_s=self.moodle.retry_backoff_s,
        )

    def require_gemini(self) -> GeminiConfig:
        if not self.gemini.api_key:
            raise ConfigError(_missing_message("Gemini", "M5", [_env_name("gemini", "api_key")]))
        return GeminiConfig(
            api_key=self.gemini.api_key,
            answer_model=self.gemini.answer_model,
            utility_model=self.gemini.utility_model,
            embed_model=self.gemini.embed_model,
            embed_dim=self.gemini.embed_dim,
            embed_batch_size=self.gemini.embed_batch_size,
            embed_rpm=self.gemini.embed_rpm,
            embed_items_per_minute=self.gemini.embed_items_per_minute,
            image_mode=self.gemini.image_mode,
        )

    def require_matrix(self) -> MatrixConfig:
        missing = [
            _env_name("matrix", field)
            for field in ("homeserver", "user_id", "room_ids")
            if not getattr(self.matrix, field)
        ]
        if not self.matrix.password and not self.matrix.access_token:
            missing.append(
                f"{_env_name('matrix', 'access_token')} "
                f"(preferred) or {_env_name('matrix', 'password')}"
            )
        if missing:
            raise ConfigError(_missing_message("Matrix", "M7", missing))

        rooms = [part.strip() for part in (self.matrix.room_ids or "").split(",")]
        rooms = [room for room in rooms if room]

        bypass_users = [
            part.strip() for part in (self.matrix.rate_limit_bypass_users or "").split(",")
        ]
        bypass_users = [user for user in bypass_users if user]

        assert self.matrix.homeserver is not None
        assert self.matrix.user_id is not None
        return MatrixConfig(
            homeserver=self.matrix.homeserver,
            user_id=self.matrix.user_id,
            password=self.matrix.password,
            access_token=self.matrix.access_token,
            device_id=self.matrix.device_id,
            login_identifier=self.matrix.login_identifier,
            refresh_token=self.matrix.refresh_token,
            oauth_client_id=self.matrix.oauth_client_id,
            oauth_token_endpoint=self.matrix.oauth_token_endpoint,
            device_name=self.matrix.device_name,
            room_ids=rooms,
            rate_limit_per_day=self.matrix.rate_limit_per_day,
            rate_limit_bypass_users=bypass_users,
        )


def _missing_message(subsystem: str, milestone: str, missing: list[str]) -> str:
    joined = "\n  - ".join(missing)
    return (
        f"{subsystem} is not configured (needed from milestone {milestone}).\n"
        f"Set the following in your .env file:\n  - {joined}\n"
        f"See .env.example for the full list."
    )


def load_settings() -> Settings:
    """Load settings from the environment and .env."""
    return Settings()
