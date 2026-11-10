"""Typed events a turn streams to the client (and persists for replay).

A tagged union on ``type``; every event carries the protocol version ``v``, the
``turn_id`` and a per-conversation ``seq`` (assigned when persisted). Clients must
ignore unknown types and fields, so new events never break old UIs.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from moodle_llm_chat.agent.citations import CitationRef

PROTOCOL_VERSION = 1


class _Event(BaseModel):
    v: Literal[1] = 1
    turn_id: str = ""
    seq: int = 0


class ThinkingEvent(_Event):
    type: Literal["thinking"] = "thinking"
    #: The reasoning text, or ``None`` when only an indicator may be shown.
    delta: str | None = None


class TokenEvent(_Event):
    type: Literal["token"] = "token"
    delta: str


class ToolCallEvent(_Event):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    args: dict[str, Any]
    summary: str = ""


class ToolResultEvent(_Event):
    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    summary: str = ""
    citations: list[CitationRef] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None


class QuestionEvent(_Event):
    type: Literal["question"] = "question"
    interrupt_id: str
    kind: str
    tool: str
    payload: dict[str, Any]
    error: str | None = None


class FinalEvent(_Event):
    type: Literal["final"] = "final"
    text: str
    citations: list[CitationRef] = Field(default_factory=list)
    #: True when the step or token budget forced an answer before the agent was done.
    forced: bool = False


class UsageEvent(_Event):
    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated: bool = False


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str


AgentEvent = Annotated[
    ThinkingEvent
    | TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | QuestionEvent
    | FinalEvent
    | UsageEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
