"""Test doubles shared by the unit and integration tiers."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_WORD = re.compile(r"\w+", re.UNICODE)


class ScriptedModel(BaseChatModel):
    """Plays back a script of AI messages (or callables of the history); records inputs.

    An unscripted call fails the test, so the agent's *behaviour* is pinned, not just
    its plumbing.
    """

    script: list[Any] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("model called more often than scripted")
        step = self.script.pop(0)
        message = step(messages) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=message)])


def call(name: str, args: dict[str, Any], call_id: str = "c1") -> AIMessage:
    """An AI message that calls one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def last_tool_output(messages: list[BaseMessage]) -> str:
    tool = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
    return str(tool.content) if tool is not None else ""


class HashEmbeddings(Embeddings):
    """Deterministic bag-of-words vectors: texts sharing words point the same way.

    Good enough for integration tests to assert that the relevant document ranks
    first, with no network and no model.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for word in _WORD.findall(text.casefold()):
            if len(word) < 3:
                continue
            bucket = int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def pdf_bytes(pages: list[str]) -> bytes:
    """A real PDF; an empty string makes a page with no text layer (a "scan")."""
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text)
    data = document.tobytes()
    document.close()
    return bytes(data)
