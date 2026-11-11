"""Contract tests over every registered tool, plus interaction-kind validation."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import ValidationError
from test_tools import FakeMoodle, ctx, world  # noqa: F401  (fixture re-export)

from moodle_llm_chat.access.scope import Capability
from moodle_llm_chat.agent.interactions import (
    INTERACTIONS,
    ChoicePayload,
    ConfirmPayload,
    QuestionPayload,
)
from moodle_llm_chat.agent.tools import default_registry
from moodle_llm_chat.agent.tools.base import InteractiveTool, Tool, ToolResult, ToolUI
from moodle_llm_chat.agent.tools.registry import ToolRegistry
from moodle_llm_chat.config import AgentSection
from moodle_llm_chat.index.store import Store

REGISTRY = default_registry()


@pytest.mark.parametrize("tool", REGISTRY.tools, ids=lambda t: t.name)
class TestContract:
    def test_has_name_description_and_ui(self, tool: Tool[Any]) -> None:
        assert tool.name.isidentifier()
        assert len(tool.description) > 40
        assert isinstance(tool.ui, ToolUI) and tool.ui.label and tool.ui.icon

    def test_args_schema_is_json_schema_for_the_model(self, tool: Tool[Any]) -> None:
        schema = tool.Args.model_json_schema()
        assert schema["type"] == "object"
        for prop in schema.get("properties", {}).values():
            assert "type" in prop or "anyOf" in prop or "enum" in prop or "$ref" in prop

    def test_summary_template_never_crashes(self, tool: Tool[Any]) -> None:
        assert isinstance(tool.summarize({}), str)

    def test_interactive_tools_declare_a_known_kind(self, tool: Tool[Any]) -> None:
        if isinstance(tool, InteractiveTool):
            assert tool.kind in INTERACTIONS
            assert Capability.INTERACT in tool.requires


class TestRegistry:
    def test_all_built_in_tools_are_registered(self) -> None:
        assert {t.name for t in REGISTRY.tools} == {
            "search_content",
            "search_text",
            "search_regex",
            "list_dir",
            "stat_file",
            "read_file",
            "list_documents",
            "current_datetime",
            "current_user",
            "ask_user",
        }

    def test_duplicate_registration_rejected(self) -> None:
        registry = ToolRegistry()
        tool = REGISTRY.tools[0]
        registry.register(tool)
        with pytest.raises(ValueError):
            registry.register(tool)

    def test_capabilities_filter_tools(self, world: Store) -> None:  # noqa: F811
        c = ctx(world)
        c.scope = c.scope.model_copy(update={"caps": frozenset({Capability.READ})})
        names = {t.name for t in REGISTRY.available(c)}
        assert "ask_user" not in names and "search_content" in names
        c.scope = c.scope.model_copy(update={"caps": frozenset()})
        assert {t.name for t in REGISTRY.available(c)} == {"current_datetime", "current_user"}

    def test_disabled_tools_are_hidden(self, world: Store) -> None:  # noqa: F811
        c = ctx(world)
        c.settings = AgentSection(disabled_tools=["search_regex"])
        assert "search_regex" not in {t.name for t in REGISTRY.available(c)}

    def test_manifest_for_the_ui(self) -> None:
        entry = next(m for m in REGISTRY.manifest() if m["name"] == "ask_user")
        assert entry["interactive"] is True and entry["label"]


class _Huge(Tool[Any]):
    name: ClassVar[str] = "huge"
    description: ClassVar[str] = (
        "Returns far more text than any model should ever be fed in one go."
    )
    ui: ClassVar[ToolUI] = ToolUI(label="Huge", icon="x", summary="", max_chars=50)

    async def run(self, args: Any, ctx: Any) -> ToolResult:
        return ToolResult(text="x" * 500)


class _Broken(Tool[Any]):
    name: ClassVar[str] = "broken"
    description: ClassVar[str] = "Always fails with an input error to test error conversion."
    ui: ClassVar[ToolUI] = ToolUI(label="Broken", icon="x", summary="")

    async def run(self, args: Any, ctx: Any) -> ToolResult:
        raise ValueError("bad thing\nwith detail")


class TestBinding:
    async def test_output_is_clipped_centrally(self, world: Store) -> None:  # noqa: F811
        registry = ToolRegistry()
        registry.register(_Huge())
        (bound,) = registry.build(ctx(world))
        message = await bound.ainvoke({"type": "tool_call", "name": "huge", "args": {}, "id": "c1"})
        assert len(message.content) < 200 and "gekürzt" in message.content
        assert message.artifact["truncated"] is True

    async def test_errors_become_text_for_the_model(self, world: Store) -> None:  # noqa: F811
        registry = ToolRegistry()
        registry.register(_Broken())
        (bound,) = registry.build(ctx(world))
        message = await bound.ainvoke(
            {"type": "tool_call", "name": "broken", "args": {}, "id": "c1"}
        )
        assert message.content == "Ungültige Eingabe: bad thing"

    async def test_moodle_outage_is_explained_not_raised(self, world: Store) -> None:  # noqa: F811
        tools = {t.name: t for t in REGISTRY.build(ctx(world, moodle=FakeMoodle(down=True)))}
        message = await tools["search_text"].ainvoke(
            {"type": "tool_call", "name": "search_text", "args": {"query": "VLAN"}, "id": "c1"}
        )
        assert "nicht erreichbar" in message.content

    async def test_artifact_carries_citations_and_summary(self, world: Store) -> None:  # noqa: F811
        tools = {t.name: t for t in REGISTRY.build(ctx(world))}
        message = await tools["search_text"].ainvoke(
            {"type": "tool_call", "name": "search_text", "args": {"query": "Raum"}, "id": "c1"}
        )
        assert message.artifact["citations"] == [1]
        assert message.artifact["summary"] == "„Raum“"


class TestInteractions:
    def test_question_accepts_offered_choice(self) -> None:
        payload = QuestionPayload(question="Welcher Kurs?", options=["LF7", "MA"])
        response = INTERACTIONS["question"].parse_response(
            payload, {"text": "LF7", "selected": ["LF7"]}
        )
        assert response.model_dump()["selected"] == ["LF7"]

    def test_question_rejects_unoffered_choice(self) -> None:
        payload = QuestionPayload(question="Welcher Kurs?", options=["LF7"])
        with pytest.raises(ValueError, match="offered"):
            INTERACTIONS["question"].parse_response(payload, {"text": "Hack", "selected": ["Hack"]})

    def test_question_rejects_multiple_when_single(self) -> None:
        payload = QuestionPayload(question="?", options=["a", "b"])
        with pytest.raises(ValueError, match="one"):
            INTERACTIONS["question"].parse_response(
                payload, {"text": "a, b", "selected": ["a", "b"]}
            )

    def test_question_requires_something(self) -> None:
        with pytest.raises(ValueError):
            INTERACTIONS["question"].parse_response(QuestionPayload(question="?"), {})

    def test_choice_validates_item(self) -> None:
        payload = ChoicePayload(question="Welches?", items=[{"id": "d1", "label": "Skript"}])  # type: ignore[list-item]
        with pytest.raises(ValueError):
            INTERACTIONS["choice"].parse_response(payload, {"id": "d2"})

    def test_confirm_requires_boolean(self) -> None:
        with pytest.raises(ValidationError):
            INTERACTIONS["confirm"].parse_response(
                ConfirmPayload(question="Löschen?"), {"confirmed": "vielleicht"}
            )
