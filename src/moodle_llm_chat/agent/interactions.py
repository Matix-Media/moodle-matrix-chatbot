"""Interaction kinds: what an interactive tool can ask, and what a valid answer is.

The chat UI renders by ``kind`` (``question``, ``confirm``, ``choice``); an unknown
kind degrades to a free-text prompt. Each kind validates its response against the
payload it was asked with — e.g. a chosen option must be one that was offered.

**Field names are a contract with the Moodle plugin** (``amd/src/interactions/*.js``,
``render.js::questionText``): payloads carry their prompt in ``question``; a question
is answered with ``{text, selected}``. ``tests/unit/test_interaction_wire.py`` pins them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class QuestionPayload(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    multi_select: bool = False
    allow_free_text: bool = True


class QuestionResponse(BaseModel):
    #: Free text, or — for an option click — the chosen option(s) as text.
    text: str = ""
    selected: list[str] = Field(default_factory=list)


class ConfirmPayload(BaseModel):
    question: str
    explanation: str = ""


class ConfirmResponse(BaseModel):
    confirmed: bool


class ChoiceItem(BaseModel):
    id: str
    label: str
    description: str = ""


class ChoicePayload(BaseModel):
    question: str
    items: list[ChoiceItem]


class ChoiceResponse(BaseModel):
    id: str


def _check_question(payload: QuestionPayload, response: QuestionResponse) -> None:
    if not response.text.strip() and not response.selected:
        raise ValueError("an answer or a choice is required")
    unknown = [c for c in response.selected if c not in payload.options]
    if unknown:
        raise ValueError(f"not an offered option: {unknown[0]!r}")
    if len(response.selected) > 1 and not payload.multi_select:
        raise ValueError("only one option may be chosen")
    # An option click arrives as text == the selection; only genuinely typed text counts.
    typed = response.text.strip() and response.text.strip() != ", ".join(response.selected)
    if typed and not payload.allow_free_text:
        raise ValueError("free text is not allowed here")


def _check_choice(payload: ChoicePayload, response: ChoiceResponse) -> None:
    if response.id not in {i.id for i in payload.items}:
        raise ValueError(f"not an offered item: {response.id!r}")


@dataclass(frozen=True)
class Interaction:
    kind: str
    payload_model: type[BaseModel]
    response_model: type[BaseModel]

    def parse_response(self, payload: BaseModel, raw: Any) -> BaseModel:
        response = self.response_model.model_validate(raw)
        if isinstance(payload, QuestionPayload) and isinstance(response, QuestionResponse):
            _check_question(payload, response)
        if isinstance(payload, ChoicePayload) and isinstance(response, ChoiceResponse):
            _check_choice(payload, response)
        return response


INTERACTIONS: dict[str, Interaction] = {
    i.kind: i
    for i in (
        Interaction("question", QuestionPayload, QuestionResponse),
        Interaction("confirm", ConfirmPayload, ConfirmResponse),
        Interaction("choice", ChoicePayload, ChoiceResponse),
    )
}
