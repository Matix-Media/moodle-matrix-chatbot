"""Context tools: the current date/time and who the user is."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from moodle_llm_chat.access.moodle import MoodleUnavailable
from moodle_llm_chat.access.scope import Capability
from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.tools.base import EmptyArgs, Tool, ToolResult, ToolUI
from moodle_llm_chat.agent.tools.display import (
    Column,
    FieldItem,
    FieldsBlock,
    NoticeBlock,
    TableBlock,
)

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
ROLE_LABELS = {
    "student": "Teilnehmer/in",
    "teacher": "Trainer/in ohne Bearbeitungsrecht",
    "editingteacher": "Trainer/in",
    "manager": "Manager/in",
    "coursecreator": "Kursersteller/in",
    "guest": "Gast",
}


class CurrentDatetime(Tool[EmptyArgs]):
    name: ClassVar[str] = "current_datetime"
    description: ClassVar[str] = (
        "Aktuelles Datum, Uhrzeit, Wochentag und Kalenderwoche. Nutze es für alle Fragen "
        "mit 'heute', 'morgen', 'nächste Woche' usw."
    )
    Args: ClassVar[type[EmptyArgs]] = EmptyArgs
    requires: ClassVar[frozenset[Capability]] = frozenset()
    ui: ClassVar[ToolUI] = ToolUI(label="Datum & Uhrzeit", icon="calendar", summary="")

    async def run(self, args: EmptyArgs, ctx: RunContext) -> ToolResult:
        now = ctx.now()
        iso = now.isocalendar()
        text = (
            f"{WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y}, {now:%H:%M} Uhr "
            f"({now.tzinfo}); Kalenderwoche {iso.week}/{iso.year}; "
            f"ISO {now.isoformat(timespec='minutes')}"
        )
        fields = [
            FieldItem(key="date", label="Datum", value=now.date().isoformat(), format="date"),
            FieldItem(key="weekday", label="Wochentag", value=WEEKDAYS[now.weekday()]),
            FieldItem(key="time", label="Uhrzeit", value=f"{now:%H:%M}"),
            FieldItem(key="week", label="Kalenderwoche", value=str(iso.week)),
            FieldItem(key="timezone", label="Zeitzone", value=str(now.tzinfo)),
        ]
        return ToolResult(text=text, display=[FieldsBlock(items=fields)])


class UserArgs(BaseModel):
    detail: Literal["summary", "courses", "access"] = Field(
        default="summary",
        description="'summary' = wer, 'courses' = Kurse mit Rollen und Gruppen, "
        "'access' = worauf der Nutzer Zugriff hat und warum andere Inhalte fehlen können.",
    )


class CurrentUser(Tool[UserArgs]):
    name: ClassVar[str] = "current_user"
    description: ClassVar[str] = (
        "Informationen über den fragenden Nutzer: Name, Sprache, Rollen pro Kurs, Gruppen, "
        "der aktuelle Kurs und auf welche Kurse er Zugriff hat."
    )
    Args: ClassVar[type[UserArgs]] = UserArgs
    requires: ClassVar[frozenset[Capability]] = frozenset()
    ui: ClassVar[ToolUI] = ToolUI(label="Nutzerinfo", icon="user", summary="{detail}")

    async def run(self, args: UserArgs, ctx: RunContext) -> ToolResult:
        user = ctx.user
        courses = [c for c in ctx.tree.root.children.values()]
        names = {c.course.course_id: c.course.fullname for c in courses if c.course is not None}
        lines = [f"Name: {user.fullname or 'unbekannt'}", f"Sprache: {user.lang}"]
        fields = [
            FieldItem(key="name", label="Name", value=user.fullname or "unbekannt"),
            FieldItem(key="lang", label="Sprache", value=user.lang),
        ]
        if user.course_context and user.course_context in names:
            lines.append(f"Aktueller Kurs: {names[user.course_context]}")
            fields.append(
                FieldItem(key="course", label="Aktueller Kurs", value=names[user.course_context])
            )
        if args.detail == "summary":
            teacher = sum(1 for r in user.roles.values() if {"editingteacher", "teacher"} & set(r))
            lines.append(f"Kurse mit Zugriff: {len(courses)} (davon als Lehrkraft: {teacher})")
            fields.append(
                FieldItem(key="courses", label="Kurse mit Zugriff", value=str(len(courses)))
            )
            fields.append(
                FieldItem(key="teaching", label="Davon als Lehrkraft", value=str(teacher))
            )
            return ToolResult(text="\n".join(lines), display=[FieldsBlock(items=fields)])

        groups = user.groups
        if not groups:
            try:
                groups = (await ctx.access.user_info(ctx.site, user.user_id)).get("groups", {})
            except MoodleUnavailable:
                groups = {}
        lines.append("Kurse:")
        rows: list[list[str]] = []
        for node in courses:
            if node.course is None:
                continue
            cid = node.course.course_id
            roles = ", ".join(ROLE_LABELS.get(r, r) for r in user.roles.get(cid, [])) or "—"
            group = f"; Gruppen: {', '.join(groups[cid])}" if groups.get(cid) else ""
            lines.append(f"  {node.name} ({node.course.fullname}): {roles}{group}")
            rows.append(
                [f"{node.name} ({node.course.fullname})", roles, ", ".join(groups.get(cid, []))]
            )
        columns = [
            Column(key="course", label="Kurs"),
            Column(key="roles", label="Rollen"),
            Column(key="groups", label="Gruppen"),
        ]
        display: list[FieldsBlock | TableBlock | NoticeBlock] = [
            FieldsBlock(items=fields),
            TableBlock(columns=columns, rows=rows),
        ]
        if args.detail == "access":
            note = (
                "Sichtbar sind nur Inhalte, die Moodle diesem Nutzer gerade anzeigt. "
                "Fehlende Inhalte sind meist verborgen, noch nicht freigegeben (Voraussetzungen, "
                "Zeitfenster, Gruppen) oder gehören zu Kursen, in denen er nicht "
                "eingeschrieben ist."
            )
            lines.append(f"Hinweis: {note}")
            display.append(NoticeBlock(code="access_note", text=note))
        return ToolResult(text="\n".join(lines), display=list(display))
