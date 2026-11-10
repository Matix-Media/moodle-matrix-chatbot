"""``moodle-llm-chat`` command line."""

from __future__ import annotations

import typer

from moodle_llm_chat import __version__
from moodle_llm_chat.config import ConfigError, Settings, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


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


if __name__ == "__main__":  # pragma: no cover
    app()
