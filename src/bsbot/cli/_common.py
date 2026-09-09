"""Helpers shared by every `bsbot` subcommand."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from bsbot.shared.config import Settings, load_settings


def fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def settings() -> Settings:
    """Load settings, turning a validation error into a readable message.

    Every command funnels through here, because a stack trace is the least useful
    possible response to a typo in a .env file.
    """
    try:
        return load_settings()
    except ValidationError as exc:
        lines = ["Your .env has a value bsbot cannot use:"]
        for error in exc.errors():
            field = ".".join(str(p) for p in error["loc"])
            env_var = "BSBOT_" + field.upper().replace(".", "__")
            lines.append(f"  {env_var}: {error['msg'].removeprefix('Value error, ')}")
        fail("\n".join(lines))
        raise  # unreachable; fail() exits


def write_env(values: dict[str, str], path: Path) -> None:
    """Update an env file in place, replacing only the given keys.

    ``path`` has no default on purpose (see git history for why): it used to fall
    back to a bare ``.env`` in the working directory, which is wrong for anything
    other than local dev run from a checkout — inside a container that path is
    neither writable (non-root user, nothing baked into the image) nor persistent
    (only the data volume survives a restart) when this is run inside a deployed
    container rather than a local checkout. Every caller passes
    ``settings.token_overrides_file`` explicitly instead: that path lives inside
    the persistent data volume and is loaded with priority over real env vars
    (see ``Settings.settings_customise_sources``), which a plain ``.env`` write
    would not be — a container's env vars always beat a dotenv file, so writing
    rotated tokens to ``.env`` would be silently ignored after a restart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(out) + "\n")
