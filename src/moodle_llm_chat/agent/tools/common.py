"""Shared argument handling and formatting for the built-in tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.timeparse import format_date, parse_moment
from moodle_llm_chat.index.models import Document

SNIPPET_CHARS = 500


class TimeFilter(BaseModel):
    since: str | None = Field(
        default=None,
        description="Nur Inhalte, die seit diesem Zeitpunkt geändert wurden: "
        "YYYY-MM-DD, relativ wie -7d / -2w, oder 'today'.",
    )
    until: str | None = Field(
        default=None, description="Nur Inhalte bis zu diesem Zeitpunkt (gleiches Format)."
    )


def time_bounds(args: TimeFilter, ctx: RunContext) -> tuple[int | None, int | None]:
    now = ctx.now()
    return (
        parse_moment(args.since, now=now),
        parse_moment(args.until, now=now, end_of_day=True),
    )


def scope_doc_ids(ctx: RunContext, path: str | None) -> list[str] | None:
    """Doc ids below ``path`` (``None`` = everything in scope)."""
    if not path or path.strip() in ("", "/"):
        return None
    node = ctx.tree.resolve(path)
    return [d.doc_id for d in ctx.tree.documents_under(node)]


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + " …"


def describe(ctx: RunContext, document: Document, *, page: int | None = None) -> str:
    path = ctx.tree.path_for(document.doc_id) or document.title
    where = f", S. {page}" if page else ""
    return f"{path}{where} (Stand {format_date(document.timemodified, now=ctx.now())})"
