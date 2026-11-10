"""``moodle-llm-chat`` command line."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import typer

from moodle_llm_chat import __version__
from moodle_llm_chat.config import ConfigError, Settings, load_settings
from moodle_llm_chat.crypto.keys import (
    KeyRing,
    MasterKeyError,
    generate_master_key,
    load_master_key,
)
from moodle_llm_chat.index.db import DatabaseKeyError
from moodle_llm_chat.index.store import Store, StoreError

app = typer.Typer(no_args_is_help=True, add_completion=False)
site_app = typer.Typer(no_args_is_help=True, help="Manage Moodle sites allowed to connect.")
app.add_typer(site_app, name="site")


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=1)


@contextmanager
def _open_store(settings: Settings) -> Iterator[Store]:
    try:
        ring = KeyRing(load_master_key(settings.security))
        with Store(
            settings.index_db, ring, embed_dim=settings.embedding.dim, blobs_dir=settings.blobs_dir
        ) as store:
            yield store
    except (MasterKeyError, DatabaseKeyError, StoreError) as exc:
        raise _fail(str(exc)) from exc


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    """Agentic course assistant for Moodle."""


def _doctor_lines(settings: Settings) -> list[str]:
    lines = [f"data dir: {settings.data_dir}"]
    try:
        llm = settings.require_llm()
        lines.append(
            f"chat model: {llm.provider.value} / {llm.model} "
            f"(utility: {llm.utility_model}, thinking: {llm.thinking.value})"
        )
    except ConfigError as exc:
        lines.append(f"chat model: not configured\n{_indent(str(exc))}")
    try:
        emb = settings.require_embedding()
        lines.append(f"embeddings: {emb.provider.value} / {emb.model} (dim {emb.dim})")
    except ConfigError as exc:
        lines.append(f"embeddings: not configured\n{_indent(str(exc))}")
    lines.append(f"ocr: {'enabled' if settings.ocr.enabled else 'disabled'}")
    return lines


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


@app.command()
def doctor() -> None:
    """Show which subsystems are configured, without contacting anything."""
    for line in _doctor_lines(load_settings()):
        typer.echo(line)


@app.command("gen-master-key")
def gen_master_key() -> None:
    """Print a new random master key (base64). Store it in a 0600 file."""
    typer.echo(generate_master_key())


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default from MLC_SERVER__HOST)."),
    port: int | None = typer.Option(None, help="Port (default from MLC_SERVER__PORT)."),
) -> None:
    """Run the API (ingest, websocket chat). Put a TLS proxy in front of it."""
    import uvicorn

    from moodle_llm_chat.logging import configure_logging
    from moodle_llm_chat.web.app import create_app

    settings = load_settings()
    configure_logging(settings.log_level, pretty=False)
    uvicorn.run(
        create_app(settings),
        host=host or settings.server.host,
        port=port or settings.server.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
    )


@site_app.command("add")
def site_add(
    site_id: str = typer.Argument(..., help="Short stable id, e.g. 'bs14'."),
    name: str = typer.Option(..., help="Display name."),
    url: str = typer.Option(..., help="Moodle base URL (https://...)."),
) -> None:
    """Register a site and print its secret ONCE — paste it into the plugin settings."""
    with _open_store(load_settings()) as store:
        try:
            secret = store.add_site(site_id, name=name, url=url)
        except StoreError as exc:
            raise _fail(str(exc)) from exc
    typer.echo(f"site {site_id} added. Secret (shown once, store it in the plugin):")
    typer.echo(secret)


@site_app.command("list")
def site_list() -> None:
    """List registered sites (secrets are never shown)."""
    with _open_store(load_settings()) as store:
        for site in store.sites():
            state = "revoked" if site.revoked_at else "active"
            created = datetime.fromtimestamp(site.created_at, UTC).date().isoformat()
            typer.echo(f"{site.site_id}\t{state}\t{site.url}\t{site.name}\t{created}")


@site_app.command("rotate")
def site_rotate(
    site_id: str,
    grace: int = typer.Option(3600, help="Seconds the old secret stays valid."),
) -> None:
    """Issue a new secret; the old one keeps working for --grace seconds."""
    with _open_store(load_settings()) as store:
        try:
            secret = store.rotate_site_secret(site_id, grace_s=grace)
        except StoreError as exc:
            raise _fail(str(exc)) from exc
    typer.echo(f"new secret for {site_id} (old one valid for {grace}s more):")
    typer.echo(secret)


@site_app.command("revoke")
def site_revoke(site_id: str) -> None:
    """Revoke a site immediately; all its requests and chat tokens are refused."""
    with _open_store(load_settings()) as store:
        try:
            store.revoke_site(site_id)
        except StoreError as exc:
            raise _fail(str(exc)) from exc
    typer.echo(f"site {site_id} revoked")


if __name__ == "__main__":  # pragma: no cover
    app()
