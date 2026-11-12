"""Navigation tools: the courses as folders and documents as files."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.paths import Node, NodeKind
from moodle_llm_chat.agent.timeparse import format_date
from moodle_llm_chat.agent.tools.base import Tool, ToolResult, ToolUI
from moodle_llm_chat.agent.tools.common import TimeFilter, scope_doc_ids, time_bounds
from moodle_llm_chat.agent.tools.display import (
    EntriesBlock,
    Entry,
    FieldItem,
    FieldsBlock,
    NoticeBlock,
    TextBlock,
    clip,
)
from moodle_llm_chat.agent.tools.registry import ToolInputError
from moodle_llm_chat.index.models import Document

_KIND_LABEL = {
    "inline": "Text",
    "html": "Seite",
    "file": "Datei",
    "external": "Link",
}


class PathArgs(BaseModel):
    path: str = Field(default="/", description="Pfad, z.B. '/' oder '/LF7/Woche 1'.")


class ReadArgs(BaseModel):
    path: str = Field(description="Pfad einer Datei (aus list_dir oder einem Suchtreffer).")
    offset: int = Field(default=0, ge=0, description="Ab diesem Zeichen lesen.")
    limit: int | None = Field(
        default=None, ge=200, description="Höchstens so viele Zeichen (Standard: Konfiguration)."
    )
    page: int | None = Field(default=None, ge=1, description="Nur diese Seite/Folie lesen.")


class ListDocumentsArgs(TimeFilter):
    order: Literal["newest", "oldest"] = "newest"
    path: str | None = Field(default=None, description="Nur unterhalb dieses Pfads.")
    limit: int = Field(default=20, ge=1, le=100)


def _size(document: Document) -> str:
    if document.page_count > 1:
        return f"{document.page_count} Seiten"
    if document.char_count:
        return f"{document.char_count} Zeichen"
    return "kein Text" if document.extract_version is not None else "wird verarbeitet"


NO_ACCESS = "Auf diese Datei hast du gerade keinen Zugriff."


def _notice(text: str, code: str, level: Literal["info", "warning"] = "info") -> ToolResult:
    return ToolResult(text=text, display=[NoticeBlock(level=level, code=code, text=text)])


def _entry(node: Node) -> Entry:
    if node.document is None:
        return Entry(
            name=node.name,
            type="folder",
            path=node.path,
            detail=node.course.fullname if node.course else "",
            count=len(node.children),
            modified=node.modified or None,
        )
    d = node.document
    return Entry(
        name=node.name,
        type=d.kind.value,
        path=node.path,
        pages=d.page_count if d.page_count > 1 else None,
        chars=d.char_count or None,
        modified=d.timemodified or None,
    )


def _iso(epoch: int, now: datetime) -> str:
    return datetime.fromtimestamp(epoch, now.tzinfo).isoformat(timespec="minutes")


async def _visible_children(ctx: RunContext, node: Node) -> list[Node]:
    files = [c.document for c in node.children.values() if c.document is not None]
    readable = {d.doc_id for d in await ctx.readable(files)}
    return [
        c for c in node.children.values() if c.document is None or c.document.doc_id in readable
    ]


class ListDir(Tool[PathArgs]):
    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = (
        "Listet den Inhalt eines Ordners: '/' zeigt die Kurse des Nutzers, ein Kurs seine "
        "Abschnitte, ein Abschnitt seine Aktivitäten und Dateien. Zum Navigieren, wenn der "
        "Nutzer einen Kurs, Abschnitt oder eine Datei nennt."
    )
    Args: ClassVar[type[PathArgs]] = PathArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Ordner öffnen", icon="folder", summary="{path}")

    async def run(self, args: PathArgs, ctx: RunContext) -> ToolResult:
        node = ctx.tree.resolve(args.path)
        if node.kind is NodeKind.FILE:
            raise ToolInputError(f"{node.path} ist eine Datei — nutze read_file oder stat_file")
        children = await _visible_children(ctx, node)
        if not children:
            return _notice(f"{node.path}: (leer)", "empty")
        now = ctx.now()
        lines = [f"{node.path}:"]
        for child in children:
            changed = format_date(child.modified, now=now)
            if child.document is None:
                count = len(child.children)
                extra = f" — {child.course.fullname}" if child.course else ""
                lines.append(
                    f"  [Ordner] {child.name}/ ({count} Einträge, geändert {changed}){extra}"
                )
            else:
                d = child.document
                kind = _KIND_LABEL.get(d.kind.value, d.kind.value)
                lines.append(f"  [{kind}] {child.name} — {_size(d)}, geändert {changed}")
        return ToolResult(
            text="\n".join(lines),
            display=[EntriesBlock(path=node.path, items=[_entry(c) for c in children])],
        )


class StatFile(Tool[PathArgs]):
    name: ClassVar[str] = "stat_file"
    description: ClassVar[str] = (
        "Zeigt Metadaten einer Datei (Titel, Typ, Seiten, Länge, Änderungsdatum, Link), "
        "ohne den Inhalt zu lesen."
    )
    Args: ClassVar[type[PathArgs]] = PathArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Details", icon="info", summary="{path}")

    async def run(self, args: PathArgs, ctx: RunContext) -> ToolResult:
        node = ctx.tree.resolve(args.path)
        if node.document is None:
            raise ToolInputError(f"{node.path} ist ein Ordner — nutze list_dir")
        readable = await ctx.readable([node.document])
        if not readable:
            return _notice(NO_ACCESS, "no_access", "warning")
        document = readable[0]
        n = ctx.cite(document)
        kind = _KIND_LABEL.get(document.kind.value, document.kind.value)
        fields = [
            FieldItem(key="title", label="Titel", value=document.title),
            FieldItem(
                key="kind",
                label="Art",
                value=kind + (f" ({document.mimetype})" if document.mimetype else ""),
            ),
            FieldItem(key="size", label="Umfang", value=_size(document)),
        ]
        if document.timemodified:
            fields.append(
                FieldItem(
                    key="modified",
                    label="Geändert",
                    value=_iso(document.timemodified, ctx.now()),
                    format="date",
                )
            )
        if document.module_url:
            fields.append(
                FieldItem(key="link", label="Moodle-Link", value=document.module_url, format="link")
            )
        if document.skip_reason:
            fields.append(FieldItem(key="note", label="Hinweis", value=document.skip_reason))
        lines = [
            f"[{n}] {node.path}",
            f"Titel: {document.title}",
            f"Art: {_KIND_LABEL.get(document.kind.value, document.kind.value)}"
            + (f" ({document.mimetype})" if document.mimetype else ""),
            f"Umfang: {_size(document)}",
            f"Geändert: {format_date(document.timemodified, now=ctx.now())}",
        ]
        if document.module_url:
            lines.append(f"Moodle-Link: {document.module_url}")
        if document.skip_reason:
            lines.append(f"Hinweis: Inhalt nicht (vollständig) lesbar ({document.skip_reason})")
        return ToolResult(text="\n".join(lines), citations=[n], display=[FieldsBlock(items=fields)])


class ReadFile(Tool[ReadArgs]):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Liest den Text einer Datei, auf Wunsch nur einen Ausschnitt (offset/limit) oder "
        "eine Seite. Lange Dateien abschnittsweise lesen: die Antwort nennt next_offset."
    )
    Args: ClassVar[type[ReadArgs]] = ReadArgs
    ui: ClassVar[ToolUI] = ToolUI(
        label="Datei lesen", icon="file-text", summary="{path}", max_chars=20_000
    )

    async def run(self, args: ReadArgs, ctx: RunContext) -> ToolResult:
        node = ctx.tree.resolve(args.path)
        if node.document is None:
            raise ToolInputError(f"{node.path} ist ein Ordner — nutze list_dir")
        readable = await ctx.readable([node.document])
        if not readable:
            return _notice(NO_ACCESS, "no_access", "warning")
        document = readable[0]
        segments = ctx.store.segments(ctx.site.site_id, document.doc_id)
        if args.page is not None:
            segments = [s for s in segments if s.page == args.page]
            if not segments:
                raise ToolInputError(f"Seite {args.page} gibt es in {node.path} nicht")
        if not segments:
            reason = document.skip_reason or "noch nicht verarbeitet"
            return _notice(f"{node.path} enthält keinen lesbaren Text ({reason}).", "no_text")

        parts: list[str] = []
        starts: list[tuple[int, int | None]] = []
        position = 0
        for s in segments:
            block = f"[Seite {s.page}]\n{s.text}" if s.page else s.text
            starts.append((position, s.page))
            parts.append(block)
            position += len(block) + 2
        full = "\n\n".join(parts)
        limit = args.limit or ctx.settings.read_max_chars
        window = full[args.offset : args.offset + limit]
        end = args.offset + len(window)
        first_page = next((p for pos, p in reversed(starts) if pos <= args.offset), None)
        n = ctx.cite(document, page=first_page)
        more = end < len(full)
        header = f"[{n}] {node.path} — Zeichen {args.offset}–{end} von {len(full)}"
        footer = f"\n\n(weiter mit offset={end})" if more else ""
        return ToolResult(
            text=f"{header}\n\n{window}{footer}",
            citations=[n],
            truncated=more,
            next_cursor=str(end) if more else None,
            display=[
                TextBlock(
                    text=clip(window),
                    title=node.path,
                    cite=n,
                    start=args.offset,
                    end=end,
                    total=len(full),
                )
            ],
            meta={"total_chars": len(full)},
        )


class ListDocuments(Tool[ListDocumentsArgs]):
    name: ClassVar[str] = "list_documents"
    description: ClassVar[str] = (
        "Listet Dokumente nach Änderungsdatum, z.B. 'Was ist neu diese Woche?' "
        "(since='-7d'). Optional auf einen Pfad beschränkt."
    )
    Args: ClassVar[type[ListDocumentsArgs]] = ListDocumentsArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Neueste Inhalte", icon="clock", summary="{order} {since}")

    async def run(self, args: ListDocumentsArgs, ctx: RunContext) -> ToolResult:
        since, until = time_bounds(args, ctx)
        wanted = scope_doc_ids(ctx, args.path)
        docs = [
            d
            for d in ctx.store.live_documents(ctx.site.site_id, cmids=ctx.scope.cmids)
            if ctx.tree.path_for(d.doc_id) is not None
            and (wanted is None or d.doc_id in wanted)
            and (since is None or d.timemodified >= since)
            and (until is None or d.timemodified <= until)
        ]
        docs.sort(key=lambda d: (d.timemodified, d.doc_id), reverse=args.order == "newest")
        docs = (await ctx.readable(docs))[: args.limit]
        if not docs:
            return _notice("Keine passenden Dokumente.", "no_documents")
        now = ctx.now()
        lines = [
            f"{format_date(d.timemodified, now=now)}  {ctx.tree.path_for(d.doc_id)}" for d in docs
        ]
        nodes = [ctx.tree.resolve(path) for d in docs if (path := ctx.tree.path_for(d.doc_id))]
        return ToolResult(
            text="\n".join(lines),
            display=[EntriesBlock(items=[_entry(node) for node in nodes])],
            meta={"count": len(docs)},
        )
