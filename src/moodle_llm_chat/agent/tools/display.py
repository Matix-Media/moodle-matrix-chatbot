"""Display blocks: what a tool's card in the chat shows, separate from what the model reads.

A tool returns its result twice: ``ToolResult.text`` for the model, and
``ToolResult.display`` — a list of typed blocks — for the person. The chat renders
each block by ``kind`` with one generic renderer, so a new tool composes existing
kinds and needs no UI code. A tool that sets no display gets a ``text`` block of its
model text; an unknown kind is shown generically by the plugin.

Kinds:

* ``hits`` — search results: citation number, location, page, date, snippet.
* ``entries`` — a listing of folders/files (``type`` = ``folder`` or a document kind).
* ``fields`` — label/value rows; ``format`` says how to show the value.
* ``text`` — an excerpt of a document or plain output.
* ``table`` — columns and rows of text.
* ``notice`` — an info/warning/error line, e.g. no hits or a failed call.

``key``/``code``/``type`` values let the plugin translate labels
(``toolfield:<key>``, ``toolnotice:<code>``, ``toolentry:<type>``); the German
``label``/``text`` is the fallback. Dates are epoch seconds or ISO strings so the
browser formats them in the user's locale.

Where a block points at Moodle objects it sends a ``location`` — a list of
:class:`~moodle_llm_chat.agent.paths.Segment` (``kind``, ``id``, ``name``) from the course
down — never the model's path: the plugin renders breadcrumbs from the names and links
them by id.

**Field names are a contract with the Moodle plugin** (``amd/src/tooldetails.js``,
``templates/tool_result.mustache``); ``tests/unit/test_tool_display.py`` pins them.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from moodle_llm_chat.agent.paths import Segment

#: Upper bound for a text block's text (the card is a preview, not a reader).
DISPLAY_TEXT_CHARS = 4000


class HitItem(BaseModel):
    cite: int
    location: list[Segment] = Field(default_factory=list)
    page: int | None = None
    #: Epoch seconds the document last changed.
    modified: int | None = None
    snippet: str = ""


class HitsBlock(BaseModel):
    kind: Literal["hits"] = "hits"
    items: list[HitItem] = Field(default_factory=list)


class Entry(BaseModel):
    name: str
    #: ``folder`` or a document kind (``inline``, ``html``, ``file``, ``external``).
    type: str
    #: The entry itself is the last segment.
    location: list[Segment] = Field(default_factory=list)
    #: Extra text, e.g. a course's full name.
    detail: str = ""
    #: Entries inside a folder.
    count: int | None = None
    pages: int | None = None
    chars: int | None = None
    modified: int | None = None
    cite: int | None = None


class EntriesBlock(BaseModel):
    kind: Literal["entries"] = "entries"
    #: The folder listed, if the entries are its children (empty for all courses).
    location: list[Segment] = Field(default_factory=list)
    items: list[Entry] = Field(default_factory=list)


class FieldItem(BaseModel):
    key: str
    label: str
    value: str
    #: ``text``, ``date`` (ISO date/date-time), ``link`` (URL) or ``code``.
    format: Literal["text", "date", "link", "code"] = "text"


class FieldsBlock(BaseModel):
    kind: Literal["fields"] = "fields"
    items: list[FieldItem] = Field(default_factory=list)


class TextBlock(BaseModel):
    kind: Literal["text"] = "text"
    text: str
    title: str = ""
    #: The document the text is from, if any.
    location: list[Segment] = Field(default_factory=list)
    cite: int | None = None
    #: Character range shown, when the text is part of a longer document.
    start: int | None = None
    end: int | None = None
    total: int | None = None


class Column(BaseModel):
    key: str
    label: str


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    columns: list[Column] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class NoticeBlock(BaseModel):
    kind: Literal["notice"] = "notice"
    level: Literal["info", "warning", "error"] = "info"
    code: str = ""
    text: str = ""


Block = Annotated[
    HitsBlock | EntriesBlock | FieldsBlock | TextBlock | TableBlock | NoticeBlock,
    Field(discriminator="kind"),
]


def clip(text: str, limit: int = DISPLAY_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " …"
