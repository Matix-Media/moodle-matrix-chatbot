"""Run one agent turn and translate LangGraph's stream into :mod:`events`.

The runner is transport-agnostic: it yields typed events and knows nothing about
websockets. A turn either ends with a :class:`FinalEvent`, or pauses with a
:class:`QuestionEvent` (an interactive tool called ``interrupt``); the next call with
``resume=`` continues the same LangGraph thread from its checkpoint.

Reasoning ("thinking") arrives as standard ``reasoning`` content blocks, or — from
OpenAI-compatible servers, which have no standard block — as
``additional_kwargs["reasoning_content"]`` (see :mod:`llm.openai_compat`);
``show_thinking`` decides whether its text, only an indicator, or nothing
reaches the client. The full messages — reasoning blocks and provider signatures
included — stay in the checkpoint, because stripping them breaks multi-step tool
calling on providers that verify them.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest
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
from moodle_llm_chat.agent.paths import PathNotFound, Segment
from moodle_llm_chat.agent.prompts import FORCE_ANSWER, system_prompt
from moodle_llm_chat.agent.tools.registry import ToolRegistry
from moodle_llm_chat.config import ShowThinking

log = structlog.get_logger(__name__)

EMPTY_FALLBACK = "Dazu konnte ich leider keine Antwort formulieren."
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


#: Tool arguments that name a place in the course tree.
PATH_ARGS = ("path",)


def _locations(ctx: RunContext, args: dict[str, Any]) -> dict[str, list[Segment]]:
    """The tree's reading of a call's path arguments, for the card (the plugin never parses
    paths). A path that does not resolve is left out; the tool reports it."""
    out: dict[str, list[Segment]] = {}
    for key in PATH_ARGS:
        value = args.get(key)
        if not isinstance(value, str):
            continue
        with contextlib.suppress(PathNotFound):
            out[key] = ctx.tree.location(ctx.tree.resolve(value))
    return out


class _WrapUp(AgentMiddleware):
    """Ends a turn gracefully: after ``max_steps`` model calls, or once the token
    budget is spent, the next model call gets no tools and an instruction to answer
    with what it has. History stays valid (no dangling tool calls)."""

    def __init__(self, max_steps: int, budget_spent: Callable[[], bool] | None) -> None:
        super().__init__()
        self.max_steps = max_steps
        self.budget_spent = budget_spent
        self.calls = 0
        self.forced = False

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        self.calls += 1
        if self.calls > self.max_steps or (self.budget_spent is not None and self.budget_spent()):
            self.forced = True
            base = request.system_message.content if request.system_message else ""
            request = request.override(
                tools=[], system_message=SystemMessage(content=f"{base}\n\n{FORCE_ANSWER}")
            )
        return await handler(request)


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

    def prune_thread(self, thread_id: str) -> None:
        """Drop the thread's superseded checkpoints, if the checkpointer can."""
        prune = getattr(self.checkpointer, "prune", None)
        if prune is None:
            return
        with contextlib.suppress(NotImplementedError):  # e.g. InMemorySaver in tests
            prune([thread_id])

    def _graph(self, ctx: RunContext, wrap_up: _WrapUp) -> Any:
        return create_agent(
            self.model,
            self.registry.build(ctx),
            system_prompt=system_prompt(ctx.user, ctx.tree, ctx.now()),
            checkpointer=self.checkpointer,
            middleware=[wrap_up],
        )

    def _config(self, thread_id: str) -> dict[str, Any]:
        # The wrap-up middleware ends turns; the recursion limit is only a backstop.
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 2 * self.max_steps + 6,
        }

    async def run(
        self,
        ctx: RunContext,
        *,
        thread_id: str,
        turn_id: str,
        message: str | None = None,
        resume: dict[str, Any] | None = None,
        continue_turn: bool = False,
        budget_spent: Callable[[], bool] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run, resume or continue a turn.

        Exactly one of: ``message`` (a new user message), ``resume`` (interrupt id →
        response, after a :class:`QuestionEvent`), or ``continue_turn`` (pick up an
        interrupted turn from its last checkpoint after a restart).

        ``budget_spent`` is polled before every model call; once it returns True the
        turn is wrapped up with one final, tool-less answer.
        """
        if sum((message is not None, resume is not None, continue_turn)) != 1:
            raise ValueError("pass exactly one of message, resume or continue_turn")
        wrap_up = _WrapUp(self.max_steps, budget_spent)
        graph = self._graph(ctx, wrap_up)
        config = self._config(thread_id)
        payload: Any
        if message is not None:
            payload = {"messages": [HumanMessage(content=message)]}
        elif resume is not None:
            payload = Command(resume=resume)
        else:
            payload = None  # continue from the last checkpoint
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
        text = message_text(final) or (STEP_LIMIT_FALLBACK if wrap_up.forced else EMPTY_FALLBACK)
        yield FinalEvent(
            turn_id=turn_id,
            text=text,
            citations=ctx.citations.cited_in(text),
            forced=wrap_up.forced,
        )

    # ------------------------------------------------------------------ #

    def _message_events(
        self, chunk: tuple[BaseMessage, dict[str, Any]], announced: set[str]
    ) -> list[AgentEvent]:
        message, metadata = chunk
        if metadata.get("langgraph_node") != "model":
            return []
        if not isinstance(message, AIMessageChunk | AIMessage):
            return []
        blocks = list(message.content_blocks)
        extra = message.additional_kwargs.get("reasoning_content")
        if (
            isinstance(extra, str)
            and extra
            and not any(b.get("type") == "reasoning" for b in blocks)
        ):
            # Reasoning goes first: a chunk carries either, or reasoning before text.
            blocks.insert(0, {"type": "reasoning", "reasoning": extra})
        events: list[AgentEvent] = []
        for block in blocks:
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
                                locations=_locations(ctx, call.get("args", {})),
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
            display=list(artifact.get("display", [])),
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
