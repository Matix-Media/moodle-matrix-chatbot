"""`bsbot serve` and `bsbot matrix-login` — the Matrix bot process and its
one-time OAuth device-grant login."""

from __future__ import annotations

import asyncio

import typer

from bsbot.cli._common import fail, settings, write_env
from bsbot.shared.config import ConfigError, load_settings


def serve(
    answer_all: bool = typer.Option(
        False,
        help="Also answer plain questions, not only messages addressed to the bot. "
        "Noisier; start without it.",
    ),
) -> None:
    """Run the Matrix bot (M7). Requires Matrix and Web API configuration.

    Talks to `api` over HTTP for everything — answering questions and
    embedding moderator messages — rather than opening the SQLite Store
    itself; `api` is the only process that does (specs/019-microservice-split.md).
    Pipeline tuning (decompose/step-back/crag/...) lives entirely in `api`
    now, not here — see `bsbot.api.app.create_app`.
    """
    from bsbot.shared.logging import configure_logging

    cfg = settings()
    configure_logging(cfg.log_level)
    try:
        cfg.require_matrix()  # fail fast on a bad config, not deep in the retry loop
        web = cfg.require_web()
    except ConfigError as exc:
        fail(str(exc))
        return

    from bsbot.matrix.api_client import ApiClient
    from bsbot.matrix.runner import run_bot

    async def run() -> None:
        api_client = ApiClient(web.api_url, web.api_token.get_secret_value())
        try:
            await run_bot(
                # Re-resolved on every restart attempt, not just once — see
                # run_bot's docstring for why a frozen config is exactly what
                # let a dead-on-disk refresh token get retried forever.
                lambda: load_settings().require_matrix(),
                api_client,
                store_dir=cfg.matrix_store_dir,
                answer_all=answer_all,
                persist_tokens=lambda values: write_env(values, cfg.token_overrides_file),
                ingest=api_client,
            )
        finally:
            api_client.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("stopped")


def matrix_login() -> None:
    """Log the bot in via the OAuth device grant and save the result (M7).

    Needed for homeservers using next-gen auth (matrix.org accounts created through
    account.matrix.org), which have no legacy password. Crucially this creates a
    device the bot *owns*, so it can upload its own encryption keys — a token
    borrowed from Element cannot decrypt anything, because Element's private keys
    never leave that browser.
    """
    import httpx

    from bsbot.matrix.oauth import (
        DeviceGrantError,
        discover_endpoints,
        generate_device_id,
        matrix_scope,
        poll_for_token,
        register_client,
        start_device_authorization,
    )

    cfg = settings()
    homeserver = cfg.matrix.homeserver
    if not homeserver:
        fail("Set BSBOT_MATRIX__HOMESERVER first (e.g. https://matrix.org).")
        return

    async def run() -> None:
        device_id = generate_device_id()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            endpoints = await discover_endpoints(http, homeserver)
            typer.echo(f"auth service: {endpoints['issuer']}")

            client_id = await register_client(
                http,
                endpoints["registration_endpoint"],
                "bsbot (Berufsschule assistant)",
                "https://github.com/bsbot",
            )
            auth = await start_device_authorization(
                http,
                endpoints["device_authorization_endpoint"],
                client_id,
                matrix_scope(device_id),
            )

            typer.secho("\nOpen this URL and approve the login:", bold=True)
            typer.secho(f"  {auth.verification_uri_complete}\n", fg=typer.colors.CYAN)
            typer.echo(f"  (code: {auth.user_code})")
            typer.echo("\nWaiting for approval...")

            tokens = await poll_for_token(
                http,
                endpoints["token_endpoint"],
                client_id,
                auth.device_code,
                interval=auth.interval,
                expires_in=auth.expires_in,
                sleep=asyncio.sleep,
            )

            whoami = await http.get(
                f"{homeserver.rstrip('/')}/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            resolved = whoami.json() if whoami.status_code == 200 else {}

        typer.secho("\nLogged in.", fg=typer.colors.GREEN)
        typer.echo(f"  user_id  : {resolved.get('user_id', '?')}")
        typer.echo(f"  device_id: {resolved.get('device_id', device_id)}")

        env_values = {
            "BSBOT_MATRIX__ACCESS_TOKEN": tokens.access_token,
            "BSBOT_MATRIX__DEVICE_ID": str(resolved.get("device_id", device_id)),
            "BSBOT_MATRIX__USER_ID": str(resolved.get("user_id", cfg.matrix.user_id or "")),
            "BSBOT_MATRIX__OAUTH_CLIENT_ID": client_id,
            "BSBOT_MATRIX__OAUTH_TOKEN_ENDPOINT": endpoints["token_endpoint"],
        }
        if tokens.refresh_token:
            env_values["BSBOT_MATRIX__REFRESH_TOKEN"] = tokens.refresh_token
        # The same path `serve` writes rotated tokens to (settings_customise_sources
        # gives it priority over real env vars) — not a bare `.env`, which is neither
        # writable (the container runs as a non-root user, with no .env baked into
        # the image) nor persistent (only the data volume survives a restart) when
        # this is run inside a deployed container rather than a local checkout.
        write_env(env_values, cfg.token_overrides_file)

        typer.secho(f"Saved to {cfg.token_overrides_file}.", fg=typer.colors.GREEN)
        if tokens.refresh_token:
            typer.echo(
                "A refresh token was stored, so restarts and redeploys will not need "
                "this browser flow again."
            )
        else:
            typer.secho(
                "No refresh token was issued - the bot will need re-authorising when "
                "this access token expires.",
                fg=typer.colors.YELLOW,
            )
        typer.echo("Now run: bsbot serve")

    try:
        asyncio.run(run())
    except DeviceGrantError as exc:
        fail(str(exc))
