"""`bsbot serve-api` — the internal HTTP API for `matrix`, `cron`, and the Nuxt
web chat frontend (spec 014)."""

from __future__ import annotations

import typer

from bsbot.cli._common import fail, settings
from bsbot.shared.config import ConfigError


def serve_api(
    port: int = typer.Option(8000, help="Port to listen on."),
) -> None:
    """Run the internal HTTP API for the Nuxt web chat frontend (spec 014)."""
    from bsbot.shared.logging import configure_logging

    cfg = settings()
    configure_logging(cfg.log_level)
    try:
        cfg.require_web()  # fail fast on a bad config, not deep inside a request
        cfg.require_gemini()
    except ConfigError as exc:
        fail(str(exc))
        return

    import uvicorn

    from bsbot.api import create_app

    uvicorn.run(create_app(cfg), host="0.0.0.0", port=port)
