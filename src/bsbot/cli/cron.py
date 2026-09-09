"""`bsbot cron` — the unattended sync -> index -> embed loop."""

from __future__ import annotations

import asyncio

import typer

from bsbot.cli._common import fail, settings
from bsbot.shared.config import ConfigError


def cron(
    interval_minutes: int = typer.Option(
        0, help="Minutes between cycles (0 = use BSBOT_SYNC_INTERVAL_MINUTES)."
    ),
    once: bool = typer.Option(
        False, help="Run a single sync -> index -> embed cycle and exit, instead of looping."
    ),
    follow_links: bool = typer.Option(True, help="Same as `bsbot sync --follow-links`."),
) -> None:
    """Repeatedly sync, index and embed — the unattended long-running update loop.

    Meant to run as its own process (its own container in docker-compose), not
    inside `bsbot serve` — a multi-hour crawl or a large embedding batch must
    never delay the bot answering a question in the room.

    Talks to `api` over HTTP for everything (specs/019-microservice-split.md)
    — never opens the SQLite Store itself, so no Gemini config or aliases
    file is needed here anymore; both live entirely in `api` now. `--aliases`
    is gone with it — see `bsbot index --aliases` for the local-dev
    equivalent, which is unaffected by this split.
    """
    from bsbot.cron.api_client import CronApiClient
    from bsbot.cron.sync_loop import run_cycle, run_forever
    from bsbot.shared.logging import configure_logging

    cfg = settings()
    configure_logging(cfg.log_level)
    try:
        moodle = cfg.require_moodle()
        web = cfg.require_web()
    except ConfigError as exc:
        fail(str(exc))
        return

    interval = interval_minutes or cfg.sync_interval_minutes

    async def cycle() -> None:
        api = CronApiClient(web.api_url, web.api_token.get_secret_value())
        try:
            await run_cycle(moodle, api, follow_links=follow_links)
        finally:
            api.close()

    async def run() -> None:
        if once:
            await cycle()
        else:
            await run_forever(cycle, interval_minutes=interval)

    asyncio.run(run())
