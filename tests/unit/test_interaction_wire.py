"""The interaction wire format, pinned to what local_llmchat's renderers read and send.

The plugin (amd/src/interactions/*.js, render.js::questionText) renders payloads and
answers with exactly these names. Changing one here without the plugin breaks the
chat UI silently — the browser tests caught it once; these keep it from coming back.
"""

from __future__ import annotations

import pytest

from moodle_llm_chat.agent.interactions import (
    INTERACTIONS,
    ChoicePayload,
    ConfirmPayload,
    QuestionPayload,
)
from moodle_llm_chat.agent.tools.interact import AskUser


class TestPayloadNames:
    def test_question_payload(self) -> None:
        payload = QuestionPayload(question="Welcher Kurs?", options=["LF7"], multi_select=True)
        assert payload.model_dump() == {
            "question": "Welcher Kurs?",
            "options": ["LF7"],
            "multi_select": True,
            "allow_free_text": True,
        }

    def test_confirm_payload_names_its_text_question(self) -> None:
        assert ConfirmPayload(question="Löschen?", explanation="Nicht rückgängig").model_dump() == {
            "question": "Löschen?",
            "explanation": "Nicht rückgängig",
        }

    def test_choice_payload_names_its_text_question(self) -> None:
        payload = ChoicePayload(question="Welches?", items=[{"id": "d1", "label": "Skript"}])  # type: ignore[list-item]
        dumped = payload.model_dump()
        assert dumped["question"] == "Welches?"
        assert dumped["items"] == [{"id": "d1", "label": "Skript", "description": ""}]

    def test_ask_user_emits_the_question_payload(self) -> None:
        tool = AskUser()
        payload = tool.payload(AskUser.Args(question="Q?", options=["a"], multi=True))
        assert payload.model_dump()["multi_select"] is True


class TestQuestionResponseAsTheBrowserSendsIt:
    """question.js sends {text, selected}: an option click sends both, free text only text."""

    def parse(self, payload: QuestionPayload, raw: dict[str, object]) -> dict[str, object]:
        return INTERACTIONS["question"].parse_response(payload, raw).model_dump()

    def test_option_click(self) -> None:
        payload = QuestionPayload(question="?", options=["LF7", "Anderer"])
        got = self.parse(payload, {"text": "LF7", "selected": ["LF7"]})
        assert got == {"text": "LF7", "selected": ["LF7"]}

    def test_free_text(self) -> None:
        got = self.parse(QuestionPayload(question="?"), {"text": "Netzwerke", "selected": []})
        assert got["text"] == "Netzwerke"

    def test_selected_must_be_offered(self) -> None:
        payload = QuestionPayload(question="?", options=["LF7"])
        with pytest.raises(ValueError, match="offered"):
            self.parse(payload, {"text": "x", "selected": ["Hack"]})

    def test_several_need_multi_select(self) -> None:
        payload = QuestionPayload(question="?", options=["a", "b"])
        with pytest.raises(ValueError, match="one"):
            self.parse(payload, {"text": "a, b", "selected": ["a", "b"]})
        multi = QuestionPayload(question="?", options=["a", "b"], multi_select=True)
        assert self.parse(multi, {"text": "a, b", "selected": ["a", "b"]})["selected"] == ["a", "b"]

    def test_empty_is_refused(self) -> None:
        with pytest.raises(ValueError):
            self.parse(QuestionPayload(question="?"), {"text": "", "selected": []})

    def test_what_the_model_reads_back_does_not_repeat_a_plain_option_click(self) -> None:
        tool = AskUser()
        response = INTERACTIONS["question"].parse_response(
            QuestionPayload(question="?", options=["LF7"]), {"text": "LF7", "selected": ["LF7"]}
        )
        text = tool.render_response(response)
        assert text.count("LF7") == 1

    def test_free_text_and_selection_are_both_reported(self) -> None:
        tool = AskUser()
        payload = QuestionPayload(question="?", options=["a", "b"], multi_select=True)
        response = INTERACTIONS["question"].parse_response(
            payload, {"text": "a und noch etwas", "selected": ["a"]}
        )
        text = tool.render_response(response)
        assert "a und noch etwas" in text and "Auswahl: a" in text


def test_confirm_and_choice_responses_match_the_plugin() -> None:
    assert INTERACTIONS["confirm"].parse_response(
        ConfirmPayload(question="?"), {"confirmed": True}
    ).model_dump() == {"confirmed": True}
    choice = ChoicePayload(question="?", items=[{"id": "d1", "label": "x"}])  # type: ignore[list-item]
    assert INTERACTIONS["choice"].parse_response(choice, {"id": "d1"}).model_dump() == {"id": "d1"}
