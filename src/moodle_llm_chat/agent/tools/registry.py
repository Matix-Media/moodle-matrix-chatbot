"""Tool registry: which tools exist, and which a given turn may use.

Adding a tool is one module plus a ``register`` line. :meth:`ToolRegistry.build`
turns the tools a turn may use (capabilities from the chat token, minus
``agent.disabled_tools``) into LangChain tools bound to that turn's
:class:`RunContext`. The wrapper clips output centrally, attaches the
:class:`ToolResult` as the message artifact for the UI, and turns tool errors into
text the model can react to — while letting LangGraph interrupts propagate.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ValidationError

from moodle_llm_chat.access.moodle import MoodleUnavailable
from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.paths import PathNotFound
from moodle_llm_chat.agent.tools.base import Tool, ToolResult
from moodle_llm_chat.agent.tools.display import NoticeBlock, TextBlock, clip

TRUNCATION_NOTE = "\n…[gekürzt — nutze offset/limit oder eine genauere Anfrage]"


class ToolInputError(ValueError):
    """A tool's arguments are unusable; the message is shown to the model."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> Tool[Any]:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} registered twice")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    @property
    def tools(self) -> list[Tool[Any]]:
        return list(self._tools.values())

    def available(self, ctx: RunContext) -> list[Tool[Any]]:
        disabled = set(ctx.settings.disabled_tools)
        return [
            t
            for t in self._tools.values()
            if t.name not in disabled and t.requires <= ctx.scope.caps
        ]

    def build(self, ctx: RunContext) -> list[BaseTool]:
        return [self._bind(tool, ctx) for tool in self.available(ctx)]

    def manifest(self, ctx: RunContext | None = None) -> list[dict[str, Any]]:
        """UI metadata, sent once per connection so new tools render without UI code."""
        tools = self.available(ctx) if ctx is not None else self.tools
        return [
            {
                "name": t.name,
                "label": t.ui.label,
                "icon": t.ui.icon,
                "summary": t.ui.summary,
                "interactive": hasattr(t, "kind"),
                "version": t.version,
            }
            for t in tools
        ]

    @staticmethod
    def _bind(tool: Tool[Any], ctx: RunContext) -> BaseTool:
        async def call(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            try:
                args = tool.Args.model_validate(kwargs)
                result = await tool.run(args, ctx)
            except GraphBubbleUp:
                raise
            except PathNotFound as exc:
                hint = ", ".join(exc.suggestions) if exc.suggestions else "—"
                result = _error(
                    f"Pfad nicht gefunden: {exc.missing!r}. Vorhanden/ähnlich: {hint}",
                    "path_not_found",
                    f"Nicht gefunden: {exc.missing}"
                    + (f" (ähnlich: {hint})" if exc.suggestions else ""),
                )
            except MoodleUnavailable:
                result = _error(
                    "Moodle ist gerade nicht erreichbar, daher kann ich keine Inhalte "
                    "lesen. Sag das dem Nutzer und rate nicht.",
                    "moodle_unavailable",
                    "Moodle ist gerade nicht erreichbar.",
                )
            except (ToolInputError, ValidationError, ValueError) as exc:
                detail = str(exc).splitlines()[0]
                result = _error(f"Ungültige Eingabe: {detail}", "invalid_input", detail)
            result = _with_display(result)
            text = result.text
            if len(text) > tool.ui.max_chars:
                text = text[: tool.ui.max_chars] + TRUNCATION_NOTE
                result = result.model_copy(update={"truncated": True})
            artifact = result.model_dump(mode="json")
            artifact["summary"] = tool.summarize(kwargs)
            return text, artifact

        schema: type[BaseModel] = tool.Args
        return StructuredTool.from_function(
            coroutine=call,
            name=tool.name,
            description=tool.description,
            args_schema=schema,
            response_format="content_and_artifact",
        )


def _error(text: str, code: str, shown: str) -> ToolResult:
    """A failed call: ``text`` tells the model, ``shown`` the person (as the notice detail)."""
    return ToolResult(
        text=text,
        meta={"error": code},
        display=[NoticeBlock(level="error", code=code, text=shown)],
    )


def _with_display(result: ToolResult) -> ToolResult:
    """A tool without display blocks shows its model text."""
    if result.display:
        return result
    return result.model_copy(update={"display": [TextBlock(text=clip(result.text))]})
