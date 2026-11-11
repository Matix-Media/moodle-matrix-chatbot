"""The tool framework. Every tool is one subclass; nothing else special-cases it.

* :class:`Tool` declares its argument schema (pydantic → JSON schema for the model),
  the capabilities it requires, and UI metadata for its card in the chat.
* :class:`ToolResult` is the uniform envelope every tool returns: the text the model
  sees, citation numbers, display blocks for the tool card (:mod:`.display`), and
  pagination hints.
* :class:`InteractiveTool` pauses the run (LangGraph ``interrupt``) with a typed
  payload of some interaction *kind*, and validates the user's typed response — the
  UI renders by kind, so a new interactive tool needs no new UI code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError

from moodle_llm_chat.access.scope import Capability
from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.interactions import INTERACTIONS, Interaction
from moodle_llm_chat.agent.tools.display import Block


class ToolUI(BaseModel):
    """How the chat renders a call to this tool."""

    label: str
    icon: str
    #: ``str.format`` template over the call's arguments, e.g. ``"Suche: {query}"``.
    summary: str
    #: Upper bound for the text the model sees from one call.
    max_chars: int = 6000


class ToolResult(BaseModel):
    text: str
    citations: list[int] = Field(default_factory=list)
    #: What the tool card shows; empty = a text block of ``text`` (see registry).
    display: list[Block] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EmptyArgs(BaseModel):
    pass


class Tool[ArgsT: BaseModel](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]] = EmptyArgs
    requires: ClassVar[frozenset[Capability]] = frozenset({Capability.READ})
    ui: ClassVar[ToolUI]
    version: ClassVar[int] = 1

    @abstractmethod
    async def run(self, args: ArgsT, ctx: RunContext) -> ToolResult: ...

    def summarize(self, args: dict[str, Any]) -> str:
        try:
            return self.ui.summary.format_map(_Defaults(args))
        except (KeyError, ValueError, IndexError):
            return self.ui.label


class _Defaults(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


class InteractiveTool[ArgsT: BaseModel](Tool[ArgsT]):
    """A tool that asks the user something and waits for the answer."""

    requires: ClassVar[frozenset[Capability]] = frozenset({Capability.INTERACT})
    kind: ClassVar[str]

    @abstractmethod
    def payload(self, args: ArgsT) -> BaseModel: ...

    @abstractmethod
    def render_response(self, response: BaseModel) -> str:
        """What the model reads back after the user answered."""

    @property
    def interaction(self) -> Interaction:
        return INTERACTIONS[self.kind]

    async def run(self, args: ArgsT, ctx: RunContext) -> ToolResult:
        from langgraph.types import interrupt

        payload = self.payload(args)
        error: str | None = None
        while True:
            raw = interrupt(
                {
                    "kind": self.kind,
                    "tool": self.name,
                    "payload": payload.model_dump(mode="json"),
                    "error": error,
                }
            )
            try:
                response = self.interaction.parse_response(payload, raw)
            except (ValidationError, ValueError) as exc:
                # Re-ask: each interrupt() call inside one node consumes the next resume.
                error = str(exc).splitlines()[0]
                continue
            return ToolResult(
                text=self.render_response(response),
                meta={"interaction": self.kind, "response": response.model_dump(mode="json")},
            )
