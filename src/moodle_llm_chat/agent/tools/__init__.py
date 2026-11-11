"""Built-in tools. ``default_registry()`` is what the API uses."""

from __future__ import annotations

from moodle_llm_chat.agent.tools.files import ListDir, ListDocuments, ReadFile, StatFile
from moodle_llm_chat.agent.tools.info import CurrentDatetime, CurrentUser
from moodle_llm_chat.agent.tools.interact import AskUser
from moodle_llm_chat.agent.tools.registry import ToolRegistry
from moodle_llm_chat.agent.tools.search import SearchContent, SearchRegex, SearchText


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        SearchContent(),
        SearchText(),
        SearchRegex(),
        ListDir(),
        StatFile(),
        ReadFile(),
        ListDocuments(),
        CurrentDatetime(),
        CurrentUser(),
        AskUser(),
    ):
        registry.register(tool)
    return registry


__all__ = ["ToolRegistry", "default_registry"]
