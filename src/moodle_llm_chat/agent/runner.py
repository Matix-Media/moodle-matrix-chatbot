"""Run one agent turn and translate LangGraph's stream into :mod:`events`.

The runner is transport-agnostic: it yields typed events and knows nothing about
websockets. A turn either ends with a :class:`FinalEvent`, or pauses with a
:class:`QuestionEvent` (an interactive tool called ``interrupt``); the next call with
``resume=`` continues the same LangGraph thread from its checkpoint.

Reasoning ("thinking") arrives as standard ``reasoning`` content blocks whatever the
provider; ``show_thinking`` decides whether its text, only an indicator, or nothing
reaches the client. The full messages — reasoning blocks and provider signatures
included — stay in the checkpoint, because stripping them breaks multi-step tool
calling on providers that verify them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, Interrupt

from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.events import (
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    QuestionEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from moodle_llm_chat.agent.prompts import FORCE_ANSWER, system_prompt
from moodle_llm_chat.agent.tools.registry import ToolRegistry
from moodle_llm_chat.config import ShowThinking

log = structlog.get_logger(__name__)

STEP_LIMIT_FALLBACK = (
    "Ich konnte die Frage mit der verfügbaren Anzahl an Arbeitsschritten nicht "
    "abschließend beantworten. Formuliere sie bitte genauer oder frag nach einem Teil davon."
)


def message_text(message: BaseMessage) -> str:
    """Concatenated text blocks (never reasoning)."""
    return "".join(
        str(b.get("text", "")) for b in message.content_blocks if b.get("type") == "text"
    )


def _usage(message: AIMessage) -> UsageEvent | None:
    meta = message.usage_metadata
    if not meta:
        return None
    details = meta.get("output_token_details") or {}
    return UsageEvent(
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
        reasoning_tokens=int(details.get("reasoning", 0) or 0),
    )


class AgentRunner:
    def __init__(
        self,
        *,
        model: BaseChatModel,
        registry: ToolRegistry,
        checkpointer: Any,
        max_steps: int,
        show_thinking: ShowThinking,
        estimate_tokens: Callable[[str], int] | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        self.checkpointer = checkpointer
        self.max_steps = max_steps
        self.show_thinking = show_thinking
        self._estimate = estimate_tokens

    def _graph(self, ctx: RunContext) -> Any:
        return create_agent(
            self.model,
            self.registry.build(ctx),
            system_prompt=system_prompt(ctx.user, ctx.tree, ctx.now()),
            checkpointer=self.checkpointer,
        )

    def _config(self, thread_id: str) -> dict[str, Any]:
        # Each step is a model call plus a tool call; +1 for the final answer.
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 2 * self.max_steps + 1}

    async def run(
        self,
        ctx: RunContext,
        *,
        thread_id: str,
        turn_id: str,
        message: str | None = None,
        resume: dict[str, Any] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run (or resume) a turn. ``resume`` maps interrupt ids to responses."""
        if (message is None) == (resume is None):
            raise ValueError("pass exactly one of message or resume")
        graph = self._graph(ctx)
        config = self._config(thread_id)
        payload: Any = (
            {"messages": [HumanMessage(content=message)]}
            if message is not None
            else Command(resume=resume)
        )
        interrupted = False
        thinking_announced: set[str] = set()
        try:
            async for mode, chunk in graph.astream(
                payload, config, stream_mode=["messages", "updates"]
            ):
                if mode == "messages":
                    for event in self._message_events(chunk, thinking_announced):
                        event.turn_id = turn_id
                        yield event
                    continue
                for event in self._update_events(ctx, chunk):
                    if isinstance(event, QuestionEvent):
                        interrupted = True
                    event.turn_id = turn_id
                    yield event
        except GraphRecursionError:
            log.info("agent.step_limit", thread=thread_id)
            async for event in self._forced_answer(ctx, graph, config):
                event.turn_id = turn_id
                yield event
            return
        if interrupted:
            return
        state = await graph.aget_state(config)
        final = next(
            (
                m
                for m in reversed(state.values.get("messages", []))
                if isinstance(m, AIMessage) and not m.tool_calls
            ),
            None,
        )
        if final is None:
            yield ErrorEvent(turn_id=turn_id, code="no_answer", message="Keine Antwort erzeugt.")
            return
        text = message_text(final)
        yield FinalEvent(turn_id=turn_id, text=text, citations=ctx.citations.cited_in(text))

    # ------------------------------------------------------------------ #

    def _message_events(
        self, chunk: tuple[BaseMessage, dict[str, Any]], announced: set[str]
    ) -> list[AgentEvent]:
        message, metadata = chunk
        if metadata.get("langgraph_node") != "model":
            return []
        if not isinstance(message, AIMessageChunk | AIMessage):
            return []
        events: list[AgentEvent] = []
        for block in message.content_blocks:
            kind = block.get("type")
            if kind == "reasoning" and self.show_thinking is not ShowThinking.OFF:
                if self.show_thinking is ShowThinking.FULL:
                    text = str(block.get("reasoning", ""))
                    if text:
                        events.append(ThinkingEvent(delta=text))
                elif message.id not in announced:
                    announced.add(message.id or "")
                    events.append(ThinkingEvent(delta=None))
            elif kind == "text":
                text = str(block.get("text", ""))
                if text:
                    events.append(TokenEvent(delta=text))
        return events

    def _update_events(self, ctx: RunContext, update: dict[str, Any]) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for node, value in update.items():
            if node == "__interrupt__":
                for item in value:
                    events.append(self._question(item))
                continue
            if not isinstance(value, dict):
                continue
            for message in value.get("messages", []):
                if isinstance(message, AIMessage):
                    usage = _usage(message)
                    if usage is None and self._estimate is not None:
                        usage = UsageEvent(
                            output_tokens=self._estimate(message_text(message)), estimated=True
                        )
                    if usage is not None:
                        events.append(usage)
                    for call in message.tool_calls:
                        tool = self.registry.get(call["name"])
                        events.append(
                            ToolCallEvent(
                                id=call.get("id") or "",
                                name=call["name"],
                                args=call.get("args", {}),
                                summary=tool.summarize(call.get("args", {})) if tool else "",
                            )
                        )
                elif isinstance(message, ToolMessage):
                    events.append(self._tool_result(ctx, message))
        return events

    @staticmethod
    def _question(item: Interrupt) -> QuestionEvent:
        value = (
            item.value
            if isinstance(item.value, dict)
            else {"payload": {"question": str(item.value)}}
        )
        return QuestionEvent(
            interrupt_id=str(item.id),
            kind=str(value.get("kind", "question")),
            tool=str(value.get("tool", "")),
            payload=dict(value.get("payload", {})),
            error=str(value["error"]) if value.get("error") else None,
        )

    @staticmethod
    def _tool_result(ctx: RunContext, message: ToolMessage) -> ToolResultEvent:
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        refs = [ref for n in artifact.get("citations", []) if (ref := ctx.citations.get(int(n)))]
        meta = artifact.get("meta") or {}
        return ToolResultEvent(
            id=message.tool_call_id,
            name=message.name or "",
            summary=str(artifact.get("summary", "")),
            citations=refs,
            attachments=list(artifact.get("attachments", [])),
            truncated=bool(artifact.get("truncated", False)),
            error=meta.get("error"),
        )

    async def _forced_answer(
        self, ctx: RunContext, graph: Any, config: dict[str, Any]
    ) -> AsyncIterator[AgentEvent]:
        state = await graph.aget_state(config)
        history: list[BaseMessage] = list(state.values.get("messages", []))
        prompt = [
            SystemMessage(content=system_prompt(ctx.user, ctx.tree, ctx.now())),
            *history,
            HumanMessage(content=FORCE_ANSWER),
        ]
        answer = await self.model.ainvoke(prompt)
        if not isinstance(answer, AIMessage):
            answer = AIMessage(content=str(answer.content))
        text = message_text(answer) or STEP_LIMIT_FALLBACK
        yield TokenEvent(delta=text)
        usage = _usage(answer)
        if usage is not None:
            yield usage
        await graph.aupdate_state(config, {"messages": [AIMessage(content=text)]}, as_node="model")
        yield FinalEvent(text=text, citations=ctx.citations.cited_in(text), forced=True)
