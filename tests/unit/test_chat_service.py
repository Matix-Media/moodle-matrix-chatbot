"""ChatService: turns as background tasks, persisted event log, replay, quotas."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from test_agent import ScriptedModel, call
from test_tools import NOW, FakeMoodle, world  # noqa: F401  (fixture re-export)

from moodle_llm_chat.access.scope import AccessScope, Capability, QuotaClaim, UserProfile
from moodle_llm_chat.access.tokens import VerifiedToken
from moodle_llm_chat.agent.checkpoint import EncryptedCheckpointer
from moodle_llm_chat.agent.runner import AgentRunner
from moodle_llm_chat.agent.tools import default_registry
from moodle_llm_chat.chat.service import ChatError, ChatService
from moodle_llm_chat.config import AgentSection, QuotaSection, ShowThinking
from moodle_llm_chat.index.search import Searcher
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.quota.service import QuotaPolicy, QuotaService

S = "site-a"


def verified(store: Store, user: int = 7, *, quota: QuotaClaim | None = None) -> VerifiedToken:
    return VerifiedToken(
        site=store.site(S),
        scope=AccessScope(
            site_id=S,
            user_id=user,
            course_ids=frozenset({2, 3}),
            cmids=frozenset({10, 11, 20}),
            caps=frozenset({Capability.READ, Capability.INTERACT}),
        ),
        user=UserProfile(user_id=user, fullname="Erika"),
        quota=quota,
        claims={},
    )


def service(store: Store, model: ScriptedModel, **kw: Any) -> ChatService:
    quota = QuotaService(
        store, QuotaSection(min_headroom_tokens=10), timezone="Europe/Berlin", clock=lambda: NOW
    )
    return ChatService(
        store=store,
        runner=AgentRunner(
            model=model,
            registry=default_registry(),
            checkpointer=EncryptedCheckpointer(store),
            max_steps=6,
            show_thinking=ShowThinking.INDICATOR,
        ),
        searcher=Searcher(store, None),
        access=kw.pop("access", FakeMoodle()),
        quota=quota,
        agent_settings=AgentSection(),
        clock=lambda: NOW,
    )


async def wait_done(svc: ChatService, conv: str) -> None:
    for _ in range(500):
        if not svc.is_running(conv):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


class TestTurns:
    async def test_send_runs_in_background_and_persists_everything(self, world: Store) -> None:  # noqa: F811
        model = ScriptedModel(
            script=[call("search_text", {"query": "Raum"}), AIMessage(content="Raum B204 [1].")]
        )
        svc = service(world, model)
        v = verified(world)
        conv, turn = await svc.send(
            v, conversation_id=None, course_id=2, text="Wo ist der Unterricht?"
        )
        await wait_done(svc, conv)
        events = svc.replay(v, conv, after_seq=0)
        assert kinds(events)[0] == "user_message"
        assert "tool_call" in kinds(events) and "final" in kinds(events)
        assert kinds(events)[-1] == "quota"
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert all(e["turn_id"] == turn for e in events)
        assert world.conversation(S, 7, conv).title == "Wo ist der Unterricht?"
        assert world.turn(conv, turn).state == "done"

    async def test_live_subscribers_get_events_as_they_happen(self, world: Store) -> None:  # noqa: F811
        model = ScriptedModel(script=[AIMessage(content="Hallo")])
        svc = service(world, model)
        v = verified(world)
        conv = world.create_conversation(S, 7)
        queue = svc.subscribe(conv)
        await svc.send(v, conversation_id=conv, course_id=None, text="Hi")
        await wait_done(svc, conv)
        seen = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        assert "final" in kinds(seen)
        svc.unsubscribe(conv, queue)

    async def test_resend_with_same_client_id_is_deduplicated(self, world: Store) -> None:  # noqa: F811
        model = ScriptedModel(script=[AIMessage(content="eins")])
        svc = service(world, model)
        v = verified(world)
        conv, t1 = await svc.send(
            v, conversation_id=None, course_id=None, text="x", client_msg_id="m1"
        )
        await wait_done(svc, conv)
        conv2, t2 = await svc.send(
            v, conversation_id=conv, course_id=None, text="x", client_msg_id="m1"
        )
        assert (conv2, t2) == (conv, t1)
        assert len(model.seen) == 1

    async def test_one_turn_at_a_time(self, world: Store) -> None:  # noqa: F811
        started = asyncio.Event()

        class Blocking(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                started.set()
                await asyncio.sleep(10)

        svc = service(world, Blocking())
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="a")
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(ChatError) as err:
            await svc.send(v, conversation_id=conv, course_id=None, text="b")
        assert err.value.code == "busy"
        await svc.cancel(v, conversation_id=conv)
        await wait_done(svc, conv)

    async def test_other_users_conversation_is_not_found(self, world: Store) -> None:  # noqa: F811
        svc = service(world, ScriptedModel(script=[AIMessage(content="x")]))
        conv, _ = await svc.send(
            verified(world, 7), conversation_id=None, course_id=None, text="mein"
        )
        await wait_done(svc, conv)
        with pytest.raises(ChatError) as err:
            svc.replay(verified(world, 8), conv, after_seq=0)
        assert err.value.code == "not_found"
        with pytest.raises(ChatError):
            await svc.send(verified(world, 8), conversation_id=conv, course_id=None, text="fremd")

    async def test_message_length_limit(self, world: Store) -> None:  # noqa: F811
        svc = service(world, ScriptedModel())
        with pytest.raises(ChatError) as err:
            await svc.send(verified(world), conversation_id=None, course_id=None, text="x" * 10_001)
        assert err.value.code == "too_long"

    async def test_model_failure_becomes_an_error_event(self, world: Store) -> None:  # noqa: F811
        def boom(messages: list[BaseMessage]) -> AIMessage:
            raise RuntimeError("provider down")

        svc = service(world, ScriptedModel(script=[boom]))
        v = verified(world)
        conv, turn = await svc.send(v, conversation_id=None, course_id=None, text="?")
        await wait_done(svc, conv)
        events = svc.replay(v, conv, after_seq=0)
        assert "error" in kinds(events)
        assert "provider down" not in str(events)  # internals are not shown to users
        assert world.turn(conv, turn).state == "failed"


class TestQuestions:
    async def test_answer_resumes_the_turn(self, world: Store) -> None:  # noqa: F811
        model = ScriptedModel(
            script=[
                call("ask_user", {"question": "Welcher Kurs?", "options": ["LF7", "MA"]}),
                AIMessage(content="LF7 also."),
            ]
        )
        svc = service(world, model)
        v = verified(world)
        conv, turn = await svc.send(v, conversation_id=None, course_id=None, text="Prüfung?")
        await wait_done(svc, conv)
        question = next(e for e in svc.replay(v, conv, after_seq=0) if e["type"] == "question")
        assert world.turn(conv, turn).state == "waiting"
        await svc.answer(
            v,
            conversation_id=conv,
            interrupt_id=question["interrupt_id"],
            response={"choices": ["LF7"]},
        )
        await wait_done(svc, conv)
        events = svc.replay(v, conv, after_seq=question["seq"])
        assert kinds(events)[0] == "user_answer"
        assert events[-2]["type"] == "final" and events[-2]["text"] == "LF7 also."
        assert world.turn(conv, turn).state == "done"

    async def test_question_survives_a_restart(self, world: Store) -> None:  # noqa: F811
        script = [
            call("ask_user", {"question": "Welcher Kurs?", "options": ["LF7"]}),
            AIMessage(content="ok"),
        ]
        model = ScriptedModel(script=list(script))
        v = verified(world)
        first = service(world, model)
        conv, _ = await first.send(v, conversation_id=None, course_id=None, text="?")
        await wait_done(first, conv)
        question = next(e for e in first.replay(v, conv, after_seq=0) if e["type"] == "question")
        # A brand-new service and checkpointer, as after a process restart.
        second = service(world, model)
        await second.recover()
        await second.answer(
            v,
            conversation_id=conv,
            interrupt_id=question["interrupt_id"],
            response={"choices": ["LF7"]},
        )
        await wait_done(second, conv)
        assert any(e["type"] == "final" for e in second.replay(v, conv, after_seq=question["seq"]))

    async def test_answer_without_pending_question_is_rejected(self, world: Store) -> None:  # noqa: F811
        svc = service(world, ScriptedModel(script=[AIMessage(content="x")]))
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="?")
        await wait_done(svc, conv)
        with pytest.raises(ChatError) as err:
            await svc.answer(v, conversation_id=conv, interrupt_id="nope", response={"answer": "x"})
        assert err.value.code == "no_question"


class TestRecovery:
    async def test_running_turns_are_marked_interrupted_and_can_continue(
        self,
        world: Store,  # noqa: F811
    ) -> None:
        model = ScriptedModel(script=[AIMessage(content="weiter geht's")])
        v = verified(world)
        conv = world.create_conversation(S, 7)
        world.start_turn(S, 7, conv, "dead-turn")  # a crash left it "running"
        svc = service(world, model)
        await svc.recover()
        assert world.turn(conv, "dead-turn").state == "interrupted"
        assert kinds(svc.replay(v, conv, after_seq=0)) == ["turn_interrupted"]

    async def test_cancel(self, world: Store) -> None:  # noqa: F811
        started = asyncio.Event()

        class Hanging(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                started.set()
                await asyncio.sleep(10)

        svc = service(world, Hanging())
        v = verified(world)
        conv, turn = await svc.send(v, conversation_id=None, course_id=None, text="?")
        await asyncio.wait_for(started.wait(), 2)
        await svc.cancel(v, conversation_id=conv)
        await wait_done(svc, conv)
        assert world.turn(conv, turn).state == "cancelled"
        assert kinds(svc.replay(v, conv, after_seq=0))[-1] == "cancelled"


class TestQuota:
    async def test_exhausted_user_is_refused_up_front(self, world: Store) -> None:  # noqa: F811
        svc = service(world, ScriptedModel())
        v = verified(world, quota=QuotaClaim(day=5))
        with pytest.raises(ChatError) as err:
            await svc.send(v, conversation_id=None, course_id=None, text="?")
        assert err.value.code == "rate_limited"
        assert err.value.details["scope"] == "user"

    async def test_usage_is_metered_and_reported(self, world: Store) -> None:  # noqa: F811
        model = ScriptedModel(
            script=[
                AIMessage(
                    content="ok",
                    usage_metadata={"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
                )
            ]
        )
        svc = service(world, model)
        v = verified(world, quota=QuotaClaim(day=1000))
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="?")
        await wait_done(svc, conv)
        quota_event = svc.replay(v, conv, after_seq=0)[-1]
        assert quota_event["type"] == "quota"
        assert quota_event["used_today"] == 50 and quota_event["limit_today"] == 1000

    async def test_turn_cap_wraps_up_gracefully(self, world: Store) -> None:  # noqa: F811
        def step(messages: list[BaseMessage]) -> AIMessage:
            from moodle_llm_chat.agent.prompts import FORCE_ANSWER

            if any(FORCE_ANSWER in str(m.content) for m in messages):
                return AIMessage(
                    content="Kurz.",
                    usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )
            return AIMessage(
                content="",
                tool_calls=[{"name": "current_datetime", "args": {}, "id": f"c{len(messages)}"}],
                usage_metadata={"input_tokens": 600, "output_tokens": 0, "total_tokens": 600},
            )

        svc = service(world, ScriptedModel(script=[step] * 6))
        svc.quota.set_policy(S, QuotaPolicy(max_tokens_per_turn=1000))
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="?")
        await wait_done(svc, conv)
        final = next(e for e in svc.replay(v, conv, after_seq=0) if e["type"] == "final")
        assert final["forced"] is True and final["text"] == "Kurz."
