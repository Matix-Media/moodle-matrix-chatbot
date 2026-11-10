"""``moodle-llm-chat`` command line."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

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
from moodle_llm_chat.quota.service import QuotaService, SiteCeilings

app = typer.Typer(no_args_is_help=True, add_completion=False)
site_app = typer.Typer(no_args_is_help=True, help="Manage Moodle sites allowed to connect.")
app.add_typer(site_app, name="site")


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=1)


@contextmanager
def _open_store(settings: Settings, *, reset_embeddings: bool = False) -> Iterator[Store]:
    try:
        ring = KeyRing(load_master_key(settings.security))
        with Store(
            settings.index_db,
            ring,
            embed_dim=settings.embedding.dim,
            blobs_dir=settings.blobs_dir,
            reset_embeddings=reset_embeddings,
        ) as store:
            yield store
    except (MasterKeyError, DatabaseKeyError, StoreError) as exc:
        raise _fail(str(exc)) from exc


def _quota(settings: Settings, store: Store) -> QuotaService:
    return QuotaService(store, settings.quota, timezone=settings.agent.timezone)


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
def gen_master_key(
    out: Path | None = typer.Option(None, help="Write to this file (mode 0600) instead of stdout."),
) -> None:
    """Create a new random master key (base64)."""
    key = generate_master_key()
    if out is None:
        typer.echo(key)
        return
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key + "\n")
    typer.echo(f"master key written to {out}")


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


@site_app.command("limits")
def site_limits(
    site_id: str,
    tokens_per_day: int | None = typer.Option(None, help="Hard daily chat-token ceiling."),
    tokens_per_month: int | None = typer.Option(None, help="Hard monthly chat-token ceiling."),
    ingest_tokens_per_day: int | None = typer.Option(
        None, help="Daily embedding/OCR budget; embedding pauses once spent."
    ),
) -> None:
    """Set operator ceilings the plugin's quota settings can never exceed."""
    settings = load_settings()
    with _open_store(settings) as store:
        try:
            quota = _quota(settings, store)
            current = quota.ceilings(site_id)
            updated = current.model_copy(
                update={
                    k: v
                    for k, v in {
                        "tokens_per_day": tokens_per_day,
                        "tokens_per_month": tokens_per_month,
                        "ingest_tokens_per_day": ingest_tokens_per_day,
                    }.items()
                    if v is not None
                }
            )
            quota.set_ceilings(site_id, SiteCeilings.model_validate(updated))
        except StoreError as exc:
            raise _fail(str(exc)) from exc
    typer.echo(json.dumps(updated.model_dump()))


@app.command()
def usage(
    site: str = typer.Option(..., help="Site id."),
    period: str = typer.Option("day", help="day or month."),
) -> None:
    """Token usage per user for the current day or month."""
    if period not in ("day", "month"):
        raise _fail("period must be 'day' or 'month'")
    settings = load_settings()
    with _open_store(settings) as store:
        rows = _quota(settings, store).report(site, period=period, by="user")  # type: ignore[arg-type]
    if not rows:
        typer.echo("no usage")
    for row in rows:
        typer.echo(f"user {row['user_id']}\t{row['tokens']} tokens\t{row['calls']} calls")


@app.command()
def status(site: str = typer.Option(..., help="Site id.")) -> None:
    """Content counts, processing backlog and the last sync of a site."""
    with _open_store(load_settings()) as store:
        try:
            store.site(site)
        except StoreError as exc:
            raise _fail(str(exc)) from exc
        info = store.status(site)
        info["jobs"] = store.job_counts()
        info["pending_vectors"] = store.pending_vector_count()
    typer.echo(json.dumps(info, indent=2, default=str))


@app.command()
def reprocess(
    skipped_only: bool = typer.Option(False, help="Only documents that were skipped/failed."),
) -> None:
    """Re-extract documents on the next API start (e.g. after enabling OCR)."""
    with _open_store(load_settings()) as store:
        n = store.reset_extraction(only_skipped=skipped_only)
    typer.echo(f"{n} documents marked for reprocessing")


@app.command()
def reembed(
    yes: bool = typer.Option(False, "--yes", help="Confirm dropping all vectors."),
) -> None:
    """Drop all vectors after changing the embedding model; the API re-embeds them."""
    if not yes:
        raise _fail("this drops every stored vector; re-run with --yes")
    with _open_store(load_settings(), reset_embeddings=True) as store:
        pending = store.pending_vector_count()
    typer.echo(f"vectors dropped; {pending} chunks will be re-embedded")


@app.command("rotate-keys")
def rotate_keys(
    new_master_key_file: Path = typer.Option(..., help="File holding the new master key."),
) -> None:
    """Re-wrap all data keys and re-key the database under a new master key.

    Stop the API first. Afterwards point MLC_SECURITY__MASTER_KEY_FILE at the new file.
    """
    settings = load_settings()
    try:
        new_ring = KeyRing(
            load_master_key(
                settings.security.model_copy(update={"master_key_file": new_master_key_file})
            )
        )
    except MasterKeyError as exc:
        raise _fail(str(exc)) from exc
    with _open_store(settings) as store:
        count = store.rotate_master_key(new_ring)
    typer.echo(f"rotated: {count} data keys re-wrapped; database re-keyed")


if __name__ == "__main__":  # pragma: no cover
    app()
