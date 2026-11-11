"""Chat turns: background tasks, a persisted event log, live subscribers.

A turn is owned by an ``asyncio.Task`` here, not by any websocket. Every event is
appended (sealed, with a per-conversation ``seq``) to the store *before* it is
published, so a client that drops and reconnects gets exactly the events it missed
via :meth:`ChatService.replay`, then live ones via :meth:`ChatService.subscribe`.

* One turn per conversation at a time; a resend with the same ``client_msg_id`` is
  deduplicated.
* Quotas are checked before a turn and polled before every model call; usage is
  recorded per model call.
* ``ask_user`` leaves the turn ``waiting``; :meth:`ChatService.answer` resumes it from
  the LangGraph checkpoint — also after a restart.
* On startup, :meth:`ChatService.recover` marks turns that were running as
  ``interrupted``; :meth:`ChatService.continue_turn` resumes them from the last
  checkpoint (completed tool calls are not repeated).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

import structlog

from moodle_llm_chat.access.tokens import VerifiedToken
from moodle_llm_chat.agent.checkpoint import thread_id_for
from moodle_llm_chat.agent.citations import CitationRef, CitationRegistry
from moodle_llm_chat.agent.context import AccessChecker, RunContext
from moodle_llm_chat.agent.events import (
    CancelledEvent,
    ErrorEvent,
    QuestionEvent,
    QuotaEvent,
    TurnInterruptedEvent,
    UsageEvent,
    UserAnswerEvent,
    UserMessageEvent,
)
from moodle_llm_chat.agent.runner import AgentRunner
from moodle_llm_chat.config import AgentSection
from moodle_llm_chat.index.search import Searcher
from moodle_llm_chat.index.store import Store, StoreError
from moodle_llm_chat.quota.service import QuotaExceeded, QuotaService

log = structlog.get_logger(__name__)

MAX_MESSAGE_CHARS = 10_000
TITLE_CHARS = 60
QUEUE_SIZE = 1000


class ChatError(Exception):
    """A request the client can be told about; ``code`` is stable for the UI."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ChatService:
    def __init__(
        self,
        *,
        store: Store,
        runner: AgentRunner,
        searcher: Searcher,
        access: AccessChecker,
        quota: QuotaService,
        agent_settings: AgentSection,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.runner = runner
        self.searcher = searcher
        self.access = access
        self.quota = quota
        self.settings = agent_settings
        self.clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._shutting_down = False

    # ------------------------------------------------------------------ #
    # Subscriptions and replay
    # ------------------------------------------------------------------ #

    def subscribe(self, conversation_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.setdefault(conversation_id, set()).add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(conversation_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                del self._subs[conversation_id]

    def replay(
        self, v: VerifiedToken, conversation_id: str, *, after_seq: int
    ) -> list[dict[str, Any]]:
        try:
            return [
                event
                for _, event in self.store.events(
                    v.site.site_id, v.scope.user_id, conversation_id, after_seq=after_seq
                )
            ]
        except StoreError as exc:
            raise ChatError("not_found", "Unterhaltung nicht gefunden.") from exc

    def is_running(self, conversation_id: str) -> bool:
        task = self._tasks.get(conversation_id)
        return task is not None and not task.done()

    def _publish(self, v: VerifiedToken, conversation_id: str, event: dict[str, Any]) -> None:
        seq = self.store.append_event(
            v.site.site_id, v.scope.user_id, conversation_id, event.get("turn_id", ""), event
        )
        event = dict(event, seq=seq)
        for queue in list(self._subs.get(conversation_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stuck client: drop it; it will resume from its last seq on reconnect.
                self.unsubscribe(conversation_id, queue)

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    def _own(self, v: VerifiedToken, conversation_id: str) -> None:
        try:
            self.store.conversation(v.site.site_id, v.scope.user_id, conversation_id)
        except StoreError as exc:
            raise ChatError("not_found", "Unterhaltung nicht gefunden.") from exc

    def _check_quota(self, v: VerifiedToken) -> None:
        try:
            self.quota.check(v.site.site_id, v.scope.user_id, v.quota)
        except QuotaExceeded as exc:
            raise ChatError(
                "rate_limited",
                "Das Token-Kontingent ist aufgebraucht.",
                scope=exc.scope,
                used=exc.used,
                limit=exc.limit,
                resets_at=exc.resets_at,
            ) from exc

    async def send(
        self,
        v: VerifiedToken,
        *,
        conversation_id: str | None,
        course_id: int | None,
        text: str,
        client_msg_id: str | None = None,
    ) -> tuple[str, str]:
        """Start a turn; returns ``(conversation_id, turn_id)``."""
        text = text.strip()
        if not text:
            raise ChatError("empty", "Die Nachricht ist leer.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ChatError("too_long", f"Höchstens {MAX_MESSAGE_CHARS} Zeichen pro Nachricht.")
        site_id, user_id = v.site.site_id, v.scope.user_id
        if conversation_id is None:
            self._check_quota(v)
            conversation_id = self.store.create_conversation(site_id, user_id, course_id=course_id)
        else:
            self._own(v, conversation_id)
            if client_msg_id:
                existing = self.store.turn_for_client_message(conversation_id, client_msg_id)
                if existing is not None:
                    return conversation_id, existing
            if self.store.running_turn(conversation_id) is not None or self.is_running(
                conversation_id
            ):
                raise ChatError("busy", "In dieser Unterhaltung läuft bereits eine Antwort.")
            self._check_quota(v)

        turn_id = secrets.token_urlsafe(9)
        self.store.start_turn(
            site_id, user_id, conversation_id, turn_id, client_msg_id=client_msg_id
        )
        if not self.store.conversation(site_id, user_id, conversation_id).title:
            self.store.set_conversation_title(site_id, user_id, conversation_id, text[:TITLE_CHARS])
        self._publish(
            v,
            conversation_id,
            UserMessageEvent(
                turn_id=turn_id, text=text, course_id=course_id, client_msg_id=client_msg_id
            ).model_dump(mode="json"),
        )
        self._spawn(v, conversation_id, turn_id, message=text, course_id=course_id)
        return conversation_id, turn_id

    async def answer(
        self,
        v: VerifiedToken,
        *,
        conversation_id: str,
        interrupt_id: str,
        response: dict[str, Any],
    ) -> str:
        """Answer the pending question of an ``ask_user`` call and resume the turn."""
        self._own(v, conversation_id)
        if self.is_running(conversation_id):
            raise ChatError("busy", "Die Antwort läuft noch.")
        turn_id = self.store.running_turn(conversation_id)
        pending = self._pending_question(v, conversation_id)
        if turn_id is None or pending is None or pending["interrupt_id"] != interrupt_id:
            raise ChatError("no_question", "Es gibt keine offene Rückfrage dazu.")
        self._check_quota(v)
        self.store.set_turn_state(turn_id, "running")
        self._publish(
            v,
            conversation_id,
            UserAnswerEvent(
                turn_id=turn_id, interrupt_id=interrupt_id, response=response
            ).model_dump(mode="json"),
        )
        self._spawn(v, conversation_id, turn_id, resume={interrupt_id: response})
        return turn_id

    async def continue_turn(self, v: VerifiedToken, *, conversation_id: str, turn_id: str) -> None:
        """Resume a turn that a restart interrupted, from its last checkpoint."""
        self._own(v, conversation_id)
        try:
            turn = self.store.turn(conversation_id, turn_id)
        except StoreError as exc:
            raise ChatError("not_found", "Antwort nicht gefunden.") from exc
        if turn.state != "interrupted" or self.is_running(conversation_id):
            raise ChatError("not_interrupted", "Diese Antwort kann nicht fortgesetzt werden.")
        self._check_quota(v)
        self.store.set_turn_state(turn_id, "running")
        self._spawn(v, conversation_id, turn_id, continue_turn=True)

    async def cancel(self, v: VerifiedToken, *, conversation_id: str) -> None:
        self._own(v, conversation_id)
        task = self._tasks.get(conversation_id)
        if task is not None and not task.done():
            task.cancel()
            return
        turn_id = self.store.running_turn(conversation_id)
        if turn_id is not None:  # a waiting question: cancelling closes it
            self.store.set_turn_state(turn_id, "cancelled")
            self._publish(
                v, conversation_id, CancelledEvent(turn_id=turn_id).model_dump(mode="json")
            )

    def _pending_question(self, v: VerifiedToken, conversation_id: str) -> dict[str, Any] | None:
        pending: dict[str, Any] | None = None
        for event in self.replay(v, conversation_id, after_seq=0):
            if event["type"] == "question":
                pending = event
            elif event["type"] in ("user_answer", "final", "cancelled", "error"):
                pending = None
        return pending

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def recover(self) -> None:
        for site_id, user_id, conversation_id, turn_id in self.store.interrupt_running_turns():
            self.store.append_event(
                site_id,
                user_id,
                conversation_id,
                turn_id,
                TurnInterruptedEvent(turn_id=turn_id).model_dump(mode="json"),
            )
            log.info("chat.turn_interrupted", conversation=conversation_id)

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # Running a turn
    # ------------------------------------------------------------------ #

    def _spawn(self, v: VerifiedToken, conversation_id: str, turn_id: str, **kwargs: Any) -> None:
        task = asyncio.create_task(self._run(v, conversation_id, turn_id, **kwargs))
        self._tasks[conversation_id] = task
        task.add_done_callback(self._forgetter(conversation_id))

    def _forgetter(self, conversation_id: str) -> Callable[[asyncio.Task[None]], None]:
        def forget(task: asyncio.Task[None]) -> None:
            if self._tasks.get(conversation_id) is task:
                del self._tasks[conversation_id]

        return forget

    def _context(self, v: VerifiedToken, conversation_id: str, course_id: int | None) -> RunContext:
        refs = [
            CitationRef.model_validate(r)
            for r in self.store.citations(v.site.site_id, v.scope.user_id, conversation_id)
        ]
        user = v.user
        if course_id is not None and user.course_context is None:
            user = user.model_copy(update={"course_context": course_id})
        return RunContext(
            site=v.site,
            scope=v.scope,
            user=user,
            store=self.store,
            searcher=self.searcher,
            access=self.access,
            settings=self.settings,
            clock=self.clock,
            citations=CitationRegistry(refs),
        )

    async def _run(
        self,
        v: VerifiedToken,
        conversation_id: str,
        turn_id: str,
        *,
        course_id: int | None = None,
        **kwargs: Any,
    ) -> None:
        site_id, user_id = v.site.site_id, v.scope.user_id
        ctx = self._context(v, conversation_id, course_id)
        turn_tokens = 0

        def budget_spent() -> bool:
            return self.quota.turn_exhausted(site_id, user_id, v.quota, turn_tokens=turn_tokens)

        state = "done"
        try:
            async for event in self.runner.run(
                ctx,
                thread_id=thread_id_for(site_id, user_id, conversation_id),
                turn_id=turn_id,
                budget_spent=budget_spent,
                **kwargs,
            ):
                if isinstance(event, UsageEvent):
                    turn_tokens += self.quota.record(
                        site_id, user_id, conversation_id, turn_id, event
                    )
                    continue
                if isinstance(event, QuestionEvent):
                    state = "waiting"
                self._publish(v, conversation_id, event.model_dump(mode="json"))
        except asyncio.CancelledError:
            if self._shutting_down:
                self.store.set_turn_state(turn_id, "interrupted")
                self._publish(
                    v,
                    conversation_id,
                    TurnInterruptedEvent(turn_id=turn_id).model_dump(mode="json"),
                )
            else:
                self.store.set_turn_state(turn_id, "cancelled")
                self._publish(
                    v, conversation_id, CancelledEvent(turn_id=turn_id).model_dump(mode="json")
                )
            raise
        except Exception:
            log.exception("chat.turn_failed", conversation=conversation_id)
            self.store.set_turn_state(turn_id, "failed")
            self._publish(
                v,
                conversation_id,
                ErrorEvent(
                    turn_id=turn_id,
                    code="internal",
                    message="Bei der Antwort ist ein Fehler aufgetreten. Bitte versuch es nochmal.",
                ).model_dump(mode="json"),
            )
            return
        self.store.set_turn_state(turn_id, state)
        self.store.save_citations(
            site_id,
            user_id,
            conversation_id,
            [r.model_dump(mode="json") for r in ctx.citations.refs],
        )
        if state != "done":
            return  # waiting for the user: the turn (and its quota summary) is not over
        status = self.quota.status(site_id, user_id, v.quota)
        self._publish(
            v,
            conversation_id,
            QuotaEvent(turn_id=turn_id, **status.model_dump()).model_dump(mode="json"),
        )
