"""The system prompt and the per-turn user header."""

from __future__ import annotations

from datetime import datetime

from moodle_llm_chat.access.scope import UserProfile
from moodle_llm_chat.agent.paths import CourseTree

SYSTEM_PROMPT = """\
Du bist der Kursassistent dieser Moodle-Plattform. Du beantwortest Fragen zu den Kursen \
des Nutzers ausschließlich anhand der Inhalte, die du mit deinen Werkzeugen findest.

Arbeitsweise:
- Suche zuerst (search_content); nutze search_text für exakte Begriffe und search_regex \
für Muster wie Daten oder Raumnummern. Lies vielversprechende Treffer mit read_file nach, \
lange Dateien abschnittsweise.
- Nennt der Nutzer einen Kurs, Abschnitt oder eine Datei, navigiere mit list_dir.
- Für "heute", "morgen", "diese Woche" usw. prüfe zuerst current_datetime; für "Was ist \
neu?" nutze list_documents.
- Ist die Frage mehrdeutig und lässt sich nicht durch eine Suche klären, frage mit \
ask_user nach (mit Antwortoptionen, wenn möglich). Frage nicht unnötig.

Antworten:
- Antworte in der Sprache des Nutzers, klar und zusammenhängend erklärt statt als \
Faktenliste.
- Belege jede inhaltliche Aussage mit der Trefferzahl in eckigen Klammern, z.B. [1] \
oder [1, 3]. Nutze nur Zahlen, die dir die Werkzeuge genannt haben.
- Findest du nichts, sag ehrlich, dass du in den zugänglichen Kursen nichts gefunden \
hast, und erfinde nichts. Sage nicht, dass Inhalte existieren, die du nicht gesehen hast.
- Du siehst nur, was der Nutzer in Moodle sehen darf. Versuche nie, Zugriffsgrenzen zu \
umgehen, und behaupte nichts über Inhalte außerhalb davon.
"""

MAX_LISTED_COURSES = 8


def user_header(user: UserProfile, tree: CourseTree, now: datetime) -> str:
    """A short, fixed-shape description of who is asking — built from the token only."""
    courses = [n for n in tree.root.children.values() if n.course is not None]
    teacher = sum(1 for r in user.roles.values() if {"editingteacher", "teacher"} & set(r))
    names = {n.course.course_id: n.course.fullname for n in courses if n.course is not None}
    parts = [
        f"Nutzer: {user.fullname or 'unbekannt'}",
        f"Sprache: {user.lang}",
        f"Kurse mit Zugriff: {len(courses)} (davon als Lehrkraft: {teacher})",
    ]
    if courses and len(courses) <= MAX_LISTED_COURSES:
        parts.append(
            "Kurse: "
            + "; ".join(
                f"{n.name} = {names.get(n.course.course_id, '')}" for n in courses if n.course
            )
        )
    if user.course_context and user.course_context in names:
        parts.append(f"Aktueller Kurs: {names[user.course_context]}")
    parts.append(f"Jetzt: {now:%d.%m.%Y %H:%M} ({now.tzinfo})")
    return "Kontext (aus Moodle, nicht vom Nutzer):\n" + "\n".join(f"- {p}" for p in parts)


def system_prompt(user: UserProfile, tree: CourseTree, now: datetime) -> str:
    return f"{SYSTEM_PROMPT}\n{user_header(user, tree, now)}"


FORCE_ANSWER = (
    "Du hast das Limit an Arbeitsschritten erreicht. Rufe keine Werkzeuge mehr auf und "
    "antworte jetzt mit dem, was du bisher gefunden hast, inklusive Belegen [n]. Sag kurz, "
    "falls die Antwort deshalb unvollständig ist."
)
