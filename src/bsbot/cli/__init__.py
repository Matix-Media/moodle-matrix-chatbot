"""Command line entry point.

Every milestone exposes its capability here, so each layer is usable and
verifiable long before the Matrix bot exists. Commands are grouped by which
service they belong to (see specs/019-microservice-split.md and
specs/020-package-reorg.md): `matrix`/`cron`/`api` run `bsbot`'s three
deployed processes, `dev` holds the local research/debugging tools that
still use direct Store/Moodle access.
"""

from __future__ import annotations

import typer

from bsbot.cli import api, cron, dev, matrix

app = typer.Typer(
    add_completion=False,
    help="Berufsschule Matrix bot — Moodle-backed question answering.",
    no_args_is_help=True,
)

app.command()(dev.doctor)
app.command()(dev.whoami)
app.command()(dev.sync)
app.command()(dev.index)
app.command()(dev.embed)
app.command()(dev.search)
app.command()(dev.ask)
app.command()(dev.bench)
app.command()(dev.chat)
app.command()(dev.export)

app.command()(cron.cron)

app.command()(matrix.serve)
app.command(name="matrix-login")(matrix.matrix_login)

app.command(name="serve-api")(api.serve_api)


if __name__ == "__main__":
    app()
