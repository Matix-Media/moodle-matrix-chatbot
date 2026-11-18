"""Conversation titles from the utility model.

One short call per conversation, on its first message. The model is built with
``utility=True``, so it never thinks; reasoning blocks are ignored anyway.
"""

from __future__ import annotations

import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from moodle_llm_chat.agent.events import UsageEvent
from moodle_llm_chat.agent.runner import message_text, usage_of

#: Titles are at most this long (the plugin's list shows about one line).
TITLE_CHARS = 60

#: Only the start of a long message is sent; it is enough to name the topic.
PROMPT_MESSAGE_CHARS = 2000

INSTRUCTION = (
    "Du benennst Unterhaltungen mit einem Kursassistenten. Antworte ausschließlich mit "
    "einem kurzen, prägnanten Titel (höchstens sechs Wörter) für die folgende erste "
    "Nachricht, in der Sprache der Nachricht. Keine Anführungszeichen, kein "
    "abschließender Punkt, keine Erklärung. Beantworte die Nachricht nicht."
)

_PREFIX = re.compile(r"^(titel|title|überschrift)\s*:\s*", re.IGNORECASE)
_QUOTES = "\"'„“”‚‘’«»`"


def clean_title(raw: str) -> str | None:
    """Reduce model output to one tidy line, or ``None`` when nothing usable is left."""
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        return None
    text = lines[0].lstrip("#").strip()
    text = _PREFIX.sub("", text).strip()
    text = text.strip("*_").strip().strip(_QUOTES).strip()
    text = text.rstrip(".").strip()
    if not text:
        return None
    if len(text) > TITLE_CHARS:
        cut = text[: TITLE_CHARS - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:-") + "…"
    return text


class TitleGenerator:
    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def generate(self, message: str) -> tuple[str | None, UsageEvent | None]:
        """``(title, usage)`` for a conversation's first message."""
        response = await self._model.ainvoke(
            [
                SystemMessage(content=INSTRUCTION),
                HumanMessage(content=message[:PROMPT_MESSAGE_CHARS]),
            ]
        )
        return clean_title(message_text(response)), usage_of(response)
