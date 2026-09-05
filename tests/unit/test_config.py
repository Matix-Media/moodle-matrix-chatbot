"""Verifies spec 001 — configuration and secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bsbot.config import ConfigError, Settings


class TestEnvLoading:
    def test_nested_env_vars_populate_subsystems(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-1: BSBOT_MOODLE__BASE_URL maps to settings.moodle.base_url."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        monkeypatch.setenv("BSBOT_MOODLE__USERNAME", "student")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://moodle.example.de"
        assert settings.moodle.username == "student"

    def test_env_file_is_read(self, tmp_path: Path) -> None:
        """AC-2: values come from a .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "BSBOT_MOODLE__BASE_URL=https://from-file.example.de\nBSBOT_MOODLE__PASSWORD=hunter2\n"
        )
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://from-file.example.de"

    def test_real_env_overrides_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-2: the process environment wins over the file."""
        env_file = tmp_path / ".env"
        env_file.write_text("BSBOT_MOODLE__BASE_URL=https://from-file.example.de\n")
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://from-env.example.de")
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://from-env.example.de"

    def test_loads_with_no_configuration_at_all(self) -> None:
        """AC-3: absent credentials must not prevent Settings from being built."""
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.base_url is None
        assert settings.gemini.api_key is None
        assert settings.matrix.homeserver is None


class TestMoodleUrlNormalisation:
    @pytest.mark.parametrize(
        "raw",
        ["https://moodle.example.de/", "https://moodle.example.de///", "https://moodle.example.de"],
    )
    def test_trailing_slashes_stripped(self, raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-4: trailing slashes are removed so URL joining is unambiguous."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", raw)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://moodle.example.de"

    def test_surrounding_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-4: pasted values often carry whitespace."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "  https://moodle.example.de  ")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://moodle.example.de"

    @pytest.mark.parametrize(
        "raw", ["moodle.example.de", "ftp://moodle.example.de", "://moodle.example.de"]
    )
    def test_missing_or_bad_scheme_rejected(
        self, raw: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-5: a URL without a usable http(s) scheme fails validation, naming the field."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", raw)
        with pytest.raises(ValidationError) as exc:
            Settings(_env_file=None)  # type: ignore[call-arg]
        assert "base_url" in str(exc.value)


class TestSecretsDoNotLeak:
    def test_secrets_are_masked_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-6: passwords, tokens and API keys must never render in logs or tracebacks."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        monkeypatch.setenv("BSBOT_MOODLE__PASSWORD", "sup3rs3cret")
        monkeypatch.setenv("BSBOT_MOODLE__TOKEN", "tok3nvalue")
        monkeypatch.setenv("BSBOT_GEMINI__API_KEY", "AIzaSyFAKEKEY")
        monkeypatch.setenv("BSBOT_MATRIX__PASSWORD", "matrixpw")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        rendered = repr(settings) + str(settings) + repr(settings.moodle) + repr(settings.gemini)
        for secret in ("sup3rs3cret", "tok3nvalue", "AIzaSyFAKEKEY", "matrixpw"):
            assert secret not in rendered

    def test_secret_value_still_retrievable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-6: masking must not make the value unusable by the code that needs it."""
        monkeypatch.setenv("BSBOT_MOODLE__PASSWORD", "sup3rs3cret")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.password is not None
        assert settings.moodle.password.get_secret_value() == "sup3rs3cret"


class TestRoomIds:
    def test_comma_separated_room_ids_are_split(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-7: a comma-separated string becomes a list."""
        monkeypatch.setenv("BSBOT_MATRIX__HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("BSBOT_MATRIX__USER_ID", "@bsbot:example.org")
        monkeypatch.setenv("BSBOT_MATRIX__PASSWORD", "pw")
        monkeypatch.setenv("BSBOT_MATRIX__ROOM_IDS", " !aaa:example.org , !bbb:example.org ,, ")
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.room_ids == ["!aaa:example.org", "!bbb:example.org"]


class TestRateLimitConfig:
    """Spec 009 AC-27/AC-28: daily quota and bypass allowlist are configurable."""

    def _base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSBOT_MATRIX__HOMESERVER", "https://matrix.example.org")
        monkeypatch.setenv("BSBOT_MATRIX__USER_ID", "@bsbot:example.org")
        monkeypatch.setenv("BSBOT_MATRIX__PASSWORD", "pw")
        monkeypatch.setenv("BSBOT_MATRIX__ROOM_IDS", "!room:example.org")

    def test_daily_quota_defaults_to_five(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-27"""
        self._base(monkeypatch)
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.rate_limit_per_day == 5
        assert matrix.rate_limit_bypass_users == []

    def test_daily_quota_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-27"""
        self._base(monkeypatch)
        monkeypatch.setenv("BSBOT_MATRIX__RATE_LIMIT_PER_DAY", "20")
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.rate_limit_per_day == 20

    def test_comma_separated_bypass_users_are_split(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-28"""
        self._base(monkeypatch)
        monkeypatch.setenv(
            "BSBOT_MATRIX__RATE_LIMIT_BYPASS_USERS", " @lehrer:example.org , @mod:example.org ,, "
        )
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.rate_limit_bypass_users == ["@lehrer:example.org", "@mod:example.org"]


class TestDataDir:
    def test_data_dir_is_absolute_with_derived_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-8: callers use properties instead of re-assembling paths."""
        monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path / "data"))
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.data_dir.is_absolute()
        assert settings.blobs_dir == settings.data_dir / "blobs"
        assert settings.index_db == settings.data_dir / "index.db"
        assert settings.matrix_store_dir == settings.data_dir / "matrix_store"

    def test_relative_data_dir_is_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-8: a relative ./data must not depend on the process working directory later."""
        monkeypatch.setenv("BSBOT_DATA_DIR", "./data")
        assert Settings(_env_file=None).data_dir.is_absolute()  # type: ignore[call-arg]


class TestRequireAccessors:
    def test_require_moodle_reports_missing_vars_and_milestone(self) -> None:
        """AC-9: the error must tell the user exactly which variables to set."""
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        with pytest.raises(ConfigError) as exc:
            settings.require_moodle()
        message = str(exc.value)
        assert "BSBOT_MOODLE__BASE_URL" in message
        assert "M1" in message

    def test_require_moodle_needs_a_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-9: a base URL alone cannot authenticate."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        with pytest.raises(ConfigError) as exc:
            Settings(_env_file=None).require_moodle()  # type: ignore[call-arg]
        assert "BSBOT_MOODLE__USERNAME" in str(exc.value)
        assert "BSBOT_MOODLE__TOKEN" in str(exc.value)

    def test_require_moodle_accepts_token_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-9: a bare token is sufficient — no username needed."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        monkeypatch.setenv("BSBOT_MOODLE__TOKEN", "abc123")
        moodle = Settings(_env_file=None).require_moodle()  # type: ignore[call-arg]
        assert moodle.token is not None
        assert moodle.base_url == "https://moodle.example.de"

    def test_require_moodle_accepts_username_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-9: username+password is the M1 path."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        monkeypatch.setenv("BSBOT_MOODLE__USERNAME", "student")
        monkeypatch.setenv("BSBOT_MOODLE__PASSWORD", "pw")
        moodle = Settings(_env_file=None).require_moodle()  # type: ignore[call-arg]
        assert moodle.username == "student"
        assert moodle.service == "moodle_mobile_app"

    def test_require_gemini_names_milestone_m5(self) -> None:
        """AC-9: Gemini is only needed from M5, and the error should say so."""
        with pytest.raises(ConfigError) as exc:
            Settings(_env_file=None).require_gemini()  # type: ignore[call-arg]
        assert "BSBOT_GEMINI__API_KEY" in str(exc.value)
        assert "M5" in str(exc.value)

    def test_require_matrix_names_milestone_m7(self) -> None:
        """AC-9: Matrix is only needed from M7."""
        with pytest.raises(ConfigError) as exc:
            Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        message = str(exc.value)
        assert "BSBOT_MATRIX__HOMESERVER" in message
        assert "M7" in message

    def test_moodle_only_config_does_not_require_gemini_or_matrix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: M1 must be runnable with Moodle credentials alone."""
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        monkeypatch.setenv("BSBOT_MOODLE__TOKEN", "abc123")
        assert Settings(_env_file=None).require_moodle() is not None  # type: ignore[call-arg]


class TestMatrixAuthOptions:
    """Spec 009 AC-15..AC-17.

    matrix.org advertises m.login.password but rejects it for accounts issued through
    next-gen auth (MAS/OIDC), so a pre-issued access token must be a first-class option.
    """

    def _base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSBOT_MATRIX__HOMESERVER", "https://matrix.org")
        monkeypatch.setenv("BSBOT_MATRIX__USER_ID", "@bsbot:matrix.org")
        monkeypatch.setenv("BSBOT_MATRIX__ROOM_IDS", "!room:matrix.org")

    def test_access_token_with_device_id_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-15"""
        self._base(monkeypatch)
        monkeypatch.setenv("BSBOT_MATRIX__ACCESS_TOKEN", "syt_token")
        monkeypatch.setenv("BSBOT_MATRIX__DEVICE_ID", "ABCDEFGH")
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.access_token is not None
        assert matrix.device_id == "ABCDEFGH"
        assert matrix.password is None

    def test_access_token_without_device_id_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-17: the runner discovers the device id via /account/whoami.

        Demanding it here would make the user hunt through client settings for a
        "Session ID" that the homeserver will happily report for the token.
        """
        self._base(monkeypatch)
        monkeypatch.setenv("BSBOT_MATRIX__ACCESS_TOKEN", "syt_token")
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.device_id is None
        assert matrix.access_token is not None

    def test_password_alone_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-15"""
        self._base(monkeypatch)
        monkeypatch.setenv("BSBOT_MATRIX__PASSWORD", "pw")
        matrix = Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        assert matrix.password is not None and matrix.access_token is None

    def test_neither_credential_names_both_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._base(monkeypatch)
        with pytest.raises(ConfigError) as exc:
            Settings(_env_file=None).require_matrix()  # type: ignore[call-arg]
        message = str(exc.value)
        assert "BSBOT_MATRIX__ACCESS_TOKEN" in message
        assert "BSBOT_MATRIX__PASSWORD" in message


class TestRuntimeTokenOverrides:
    """A rotated Matrix refresh token must survive a container restart.

    MAS rotates the refresh token on every use, so the bot persists the new one
    somewhere durable. But real environment variables (which is what a container's
    ``env_file:`` becomes once the process starts) always beat dotenv file values
    in pydantic-settings' default source order — so writing the rotation to a plain
    file the app also reads as a dotenv would be silently ignored, and the bot
    would keep trying a token MAS has already invalidated. The override file must
    outrank real env vars, not just exist.
    """

    def test_override_file_beats_a_real_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("BSBOT_MATRIX__REFRESH_TOKEN", "stale-from-deploy-config")
        (tmp_path / "matrix-tokens.env").write_text(
            "BSBOT_MATRIX__REFRESH_TOKEN=fresh-after-rotation\n"
        )
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert (
            settings.matrix.refresh_token is not None
            and settings.matrix.refresh_token.get_secret_value() == "fresh-after-rotation"
        )

    def test_missing_override_file_falls_back_to_the_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("BSBOT_MATRIX__REFRESH_TOKEN", "only-source-available")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert (
            settings.matrix.refresh_token is not None
            and settings.matrix.refresh_token.get_secret_value() == "only-source-available"
        )

    def test_override_file_only_affects_keys_it_actually_sets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rotation only touches the token; other config must pass through untouched."""
        monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("BSBOT_MOODLE__BASE_URL", "https://moodle.example.de")
        (tmp_path / "matrix-tokens.env").write_text("BSBOT_MATRIX__REFRESH_TOKEN=fresh\n")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.moodle.base_url == "https://moodle.example.de"

    def test_token_overrides_path_is_inside_the_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.token_overrides_file == tmp_path.resolve() / "matrix-tokens.env"
