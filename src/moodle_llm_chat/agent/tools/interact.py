"""Interactive tools: ask the user for missing context."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from moodle_llm_chat.agent.interactions import QuestionPayload, QuestionResponse
from moodle_llm_chat.agent.tools.base import InteractiveTool, ToolUI


class AskArgs(BaseModel):
    question: str = Field(description="Die Rückfrage an den Nutzer, kurz und konkret.")
    options: list[str] = Field(
        default_factory=list, max_length=8, description="Optionale Antwortmöglichkeiten."
    )
    multi: bool = Field(default=False, description="Mehrere Optionen auswählbar.")


class AskUser(InteractiveTool[AskArgs]):
    name: ClassVar[str] = "ask_user"
    kind: ClassVar[str] = "question"
    description: ClassVar[str] = (
        "Stellt dem Nutzer eine Rückfrage, wenn die Frage mehrdeutig ist oder Kontext fehlt "
        "(z.B. welcher Kurs gemeint ist). Biete passende Optionen an, wenn möglich. Nicht "
        "nutzen, wenn eine Suche die Frage klären kann."
    )
    Args: ClassVar[type[AskArgs]] = AskArgs
    ui: ClassVar[ToolUI] = ToolUI(label="Rückfrage", icon="message-question", summary="{question}")

    def payload(self, args: AskArgs) -> QuestionPayload:
        return QuestionPayload(question=args.question, options=args.options, multi=args.multi)

    def render_response(self, response: BaseModel) -> str:
        assert isinstance(response, QuestionResponse)
        parts = []
        if response.choices:
            parts.append("Auswahl: " + ", ".join(response.choices))
        if response.answer:
            parts.append(f"Antwort: {response.answer}")
        return "Der Nutzer antwortet — " + "; ".join(parts)
