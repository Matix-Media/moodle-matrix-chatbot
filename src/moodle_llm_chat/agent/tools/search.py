"""Retrieval tools: semantic+keyword search, keyword/phrase search, regex search."""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar, Literal

from pydantic import Field

from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.tools.base import Tool, ToolResult, ToolUI
from moodle_llm_chat.agent.tools.common import (
    TimeFilter,
    describe,
    scope_doc_ids,
    snippet,
    time_bounds,
)
from moodle_llm_chat.agent.tools.display import HitItem, HitsBlock, NoticeBlock
from moodle_llm_chat.agent.tools.registry import ToolInputError
from moodle_llm_chat.index.models import Document
from moodle_llm_chat.index.search import Hit

NO_HITS = "Keine Treffer in den Kursen, auf die du Zugriff hast."
MAX_PATTERN = 200
REGEX_TIMEOUT_S = 3.0
#: Nested quantifiers like (a+)+ or (.*)* are the classic catastrophic-backtracking shape.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}]\)\s*[+*{]")


def no_hits() -> ToolResult:
    return ToolResult(text=NO_HITS, display=[NoticeBlock(code="no_hits", text=NO_HITS)])


class SearchArgs(TimeFilter):
    query: str = Field(description="Wonach gesucht wird, in eigenen Worten oder Stichworten.")
    k: int = Field(default=8, ge=1, le=20, description="Maximale Anzahl Treffer.")
    path: str | None = Field(
        default=None, description="Nur unterhalb dieses Pfads suchen, z.B. '/LF7/Woche 3'."
    )


class TextSearchArgs(SearchArgs):
    mode: Literal["any", "all", "phrase"] = Field(
        default="any",
        description="'any' = eines der Wörter, 'all' = alle Wörter, 'phrase' = exakte Wortfolge.",
    )


class RegexArgs(TimeFilter):
    pattern: str = Field(description="Regulärer Ausdruck (Python-Syntax).")
    path: str | None = Field(default=None, description="Nur unterhalb dieses Pfads suchen.")
    ignore_case: bool = True
    max_matches: int = Field(default=20, ge=1, le=50)
    context_chars: int = Field(default=120, ge=0, le=400)


async def _render_hits(ctx: RunContext, hits: list[Hit]) -> ToolResult:
    readable = {d.doc_id for d in await ctx.readable(h.document for h in hits)}
    lines: list[str] = []
    cites: list[int] = []
    items: list[HitItem] = []
    for hit in hits:
        if hit.document.doc_id not in readable:
            continue
        n = ctx.cite(hit.document, page=hit.chunk.page)
        cites.append(n)
        text = snippet(hit.chunk.text)
        lines.append(f"[{n}] {describe(ctx, hit.document, page=hit.chunk.page)}\n{text}")
        items.append(_hit(ctx, n, hit.document, hit.chunk.page, text))
    if not lines:
        return no_hits()
    return ToolResult(
        text="\n\n".join(lines),
        citations=sorted(set(cites)),
        display=[HitsBlock(items=items)],
        meta={"hits": len(lines)},
    )


def _hit(ctx: RunContext, n: int, document: Document, page: int | None, text: str) -> HitItem:
    return HitItem(
        cite=n,
        location=ctx.tree.location_for(document.doc_id),
        page=page,
        modified=document.timemodified or None,
        snippet=text,
    )


class SearchContent(Tool[SearchArgs]):
    name: ClassVar[str] = "search_content"
    description: ClassVar[str] = (
        "Durchsucht die Kursinhalte des Nutzers nach Bedeutung und Stichworten (beste "
        "erste Wahl für inhaltliche Fragen). Liefert nummerierte Treffer mit Pfad; zitiere "
        "sie im Antworttext als [n]. Für Details danach read_file mit dem Pfad nutzen."
    )
    Args: ClassVar[type[SearchArgs]] = SearchArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Inhalte durchsuchen", icon="search", summary="{query}")

    async def run(self, args: SearchArgs, ctx: RunContext) -> ToolResult:
        since, until = time_bounds(args, ctx)
        hits = await ctx.searcher.hybrid(
            ctx.scope,
            args.query,
            k=args.k,
            since=since,
            until=until,
            doc_ids=scope_doc_ids(ctx, args.path),
        )
        return await _render_hits(ctx, hits)


class SearchText(Tool[TextSearchArgs]):
    name: ClassVar[str] = "search_text"
    description: ClassVar[str] = (
        "Exakte Stichwort- oder Phrasensuche (ohne Bedeutungssuche). Gut für Namen, "
        "Fachbegriffe, Nummern oder wenn search_content zu ungenau ist."
    )
    Args: ClassVar[type[TextSearchArgs]] = TextSearchArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Textsuche", icon="text-search", summary="„{query}“")

    async def run(self, args: TextSearchArgs, ctx: RunContext) -> ToolResult:
        since, until = time_bounds(args, ctx)
        hits = await ctx.searcher.text(
            ctx.scope,
            args.query,
            k=args.k,
            mode=args.mode,
            since=since,
            until=until,
            doc_ids=scope_doc_ids(ctx, args.path),
        )
        return await _render_hits(ctx, hits)


def compile_safely(pattern: str, *, ignore_case: bool) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN:
        raise ToolInputError(f"Muster zu lang (max. {MAX_PATTERN} Zeichen)")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ToolInputError("verschachtelte Wiederholungen wie (a+)+ sind nicht erlaubt")
    try:
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolInputError(f"ungültiger regulärer Ausdruck: {exc}") from exc


class SearchRegex(Tool[RegexArgs]):
    name: ClassVar[str] = "search_regex"
    description: ClassVar[str] = (
        "Sucht mit einem regulären Ausdruck im vollständigen Text der Dokumente, z.B. "
        r"Daten (\d{1,2}\.\d{1,2}\.\d{4}), Raumnummern oder Formate. "
        "Liefert Fundstellen mit Kontext."
    )
    Args: ClassVar[type[RegexArgs]] = RegexArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Mustersuche", icon="regex", summary="/{pattern}/")

    async def run(self, args: RegexArgs, ctx: RunContext) -> ToolResult:
        regex = compile_safely(args.pattern, ignore_case=args.ignore_case)
        since, until = time_bounds(args, ctx)
        wanted = scope_doc_ids(ctx, args.path)
        docs = [
            d
            for d in ctx.store.live_documents(ctx.site.site_id, cmids=ctx.scope.cmids)
            if (wanted is None or d.doc_id in wanted)
            and (since is None or d.timemodified >= since)
            and (until is None or d.timemodified <= until)
        ]
        docs = await ctx.readable(docs)
        corpus = [(d, s) for d in docs for s in ctx.store.segments(ctx.site.site_id, d.doc_id)]

        def scan() -> list[tuple[int, int | None, str]]:
            found: list[tuple[int, int | None, str]] = []
            for index, (_doc, segment) in enumerate(corpus):
                for match in regex.finditer(segment.text):
                    start = max(0, match.start() - args.context_chars)
                    end = min(len(segment.text), match.end() + args.context_chars)
                    found.append((index, segment.page, segment.text[start:end]))
                    if len(found) >= args.max_matches:
                        return found
            return found

        try:
            found = await asyncio.wait_for(asyncio.to_thread(scan), timeout=REGEX_TIMEOUT_S)
        except TimeoutError as exc:
            raise ToolInputError("Suche dauerte zu lange; Muster vereinfachen") from exc
        if not found:
            return no_hits()
        lines, cites, items = [], [], []
        for index, page, context in found:
            document = corpus[index][0]
            n = ctx.cite(document, page=page)
            cites.append(n)
            text = f"…{' '.join(context.split())}…"
            lines.append(f"[{n}] {describe(ctx, document, page=page)}\n{text}")
            items.append(_hit(ctx, n, document, page, text))
        return ToolResult(
            text="\n\n".join(lines),
            citations=sorted(set(cites)),
            display=[HitsBlock(items=items)],
            meta={"matches": len(found), "limit_reached": len(found) >= args.max_matches},
        )
