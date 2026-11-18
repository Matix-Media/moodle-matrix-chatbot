"""Conversation titles: the utility model names a conversation after its first message.

Until it answers (or if it fails) the conversation keeps the truncated first message as
its title. A title the user set in the meantime is never overwritten. The plugin
refreshes its list when a turn's ``final`` arrives, so the final waits (briefly) for the
title; a ``title`` event announces it either way.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from test_agent import ScriptedModel
from test_chat_service import kinds, service, verified, wait_done
from test_tools import NOW, FakeMoodle, world  # noqa: F401  (fixture re-export)

from moodle_llm_chat.agent.events import TitleEvent
from moodle_llm_chat.chat.service import TITLE_CHARS
from moodle_llm_chat.chat.titles import TitleGenerator, clean_title
from moodle_llm_chat.index.store import Store

S = "site-a"


def titled(text: str, **usage: int) -> AIMessage:
    meta = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, **usage}
    meta["total_tokens"] = meta["input_tokens"] + meta["output_tokens"]
    return AIMessage(content=text, usage_metadata=meta)  # type: ignore[arg-type]


class TestCleanTitle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Raum für den Unterricht", "Raum für den Unterricht"),
            ('"Raum für den Unterricht"', "Raum für den Unterricht"),
            ("„Raum für den Unterricht“", "Raum für den Unterricht"),
            ("Titel: Prüfungstermine LF7", "Prüfungstermine LF7"),
            ("**Prüfungstermine LF7**", "Prüfungstermine LF7"),
            ("# Prüfungstermine LF7", "Prüfungstermine LF7"),
            ("Prüfungstermine LF7.", "Prüfungstermine LF7"),
            ("  Prüfungstermine\n\nweitere Zeilen", "Prüfungstermine"),
            ("", None),
            ('  ""  ', None),
        ],
    )
    def test_cleans_model_output(self, raw: str, expected: str | None) -> None:
        assert clean_title(raw) == expected

    def test_long_titles_are_cut_at_a_word(self) -> None:
        title = clean_title("Wort " * 40)
        assert title is not None
        assert len(title) <= TITLE_CHARS
        assert title.endswith("…")
        assert "Wor…" not in title


class TestTitleGenerator:
    async def test_asks_the_model_with_the_message_and_returns_title_and_usage(self) -> None:
        model = ScriptedModel(script=[titled("Raumsuche", input_tokens=40, output_tokens=3)])
        title, usage = await TitleGenerator(model).generate("Wo ist der Unterricht morgen?")
        assert title == "Raumsuche"
        assert usage is not None and usage.input_tokens == 40 and usage.output_tokens == 3
        shown = model.seen[0]
        assert isinstance(shown[-1], HumanMessage)
        assert "Wo ist der Unterricht morgen?" in str(shown[-1].content)

    async def test_ignores_reasoning_blocks(self) -> None:
        message = AIMessage(
            content=[
                {"type": "reasoning", "reasoning": "Hmm, ein Titel …"},
                {"type": "text", "text": "Raumsuche"},
            ]
        )
        title, _ = await TitleGenerator(ScriptedModel(script=[message])).generate("Wo?")
        assert title == "Raumsuche"

    async def test_long_messages_are_shortened_before_sending(self) -> None:
        model = ScriptedModel(script=[titled("Lang")])
        await TitleGenerator(model).generate("x" * 50_000)
        assert len(str(model.seen[0][-1].content)) < 5_000


def titling_service(store: Store, chat: ScriptedModel, titles: Any, **kw: Any) -> Any:
    svc = service(store, chat, **kw)
    svc.titles = TitleGenerator(titles)
    return svc


async def settle(svc: Any) -> None:
    """Wait for background title tasks too."""
    for _ in range(500):
        if not svc._title_tasks:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError


class TestTitlesInChat:
    async def test_first_message_gets_a_generated_title(self, world: Store) -> None:  # noqa: F811
        titles = ScriptedModel(script=[titled("Unterrichtsraum", input_tokens=30, output_tokens=2)])
        svc = titling_service(world, ScriptedModel(script=[AIMessage(content="B204.")]), titles)
        v = verified(world)
        conv, turn = await svc.send(
            v, conversation_id=None, course_id=None, text="Wo ist der Unterricht?"
        )
        await wait_done(svc, conv)
        await settle(svc)
        assert world.conversation(S, 7, conv).title == "Unterrichtsraum"
        events = svc.replay(v, conv, after_seq=0)
        title_event = next(e for e in events if e["type"] == "title")
        assert title_event["title"] == "Unterrichtsraum"
        assert title_event["turn_id"] == turn
        # The plugin refreshes its list on ``final``: the title must be there by then.
        assert kinds(events).index("title") < kinds(events).index("final")
        assert len(titles.seen) == 1

    async def test_title_usage_is_metered_as_utility(self, world: Store) -> None:  # noqa: F811
        titles = ScriptedModel(script=[titled("Raum", input_tokens=30, output_tokens=2)])
        svc = titling_service(world, ScriptedModel(script=[AIMessage(content="B204.")]), titles)
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="Wo?")
        await wait_done(svc, conv)
        await settle(svc)
        rows = world.usage_rows(S, since=0, until=2**40, purposes=("utility",), user_id=7)
        assert [(r["input_tokens"], r["output_tokens"]) for r in rows] == [(30, 2)]

    async def test_later_messages_do_not_retitle(self, world: Store) -> None:  # noqa: F811
        titles = ScriptedModel(script=[titled("Erster Titel")])
        chat = ScriptedModel(script=[AIMessage(content="eins"), AIMessage(content="zwei")])
        svc = titling_service(world, chat, titles)
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="eins?")
        await wait_done(svc, conv)
        await settle(svc)
        await svc.send(v, conversation_id=conv, course_id=None, text="zwei?")
        await wait_done(svc, conv)
        await settle(svc)
        assert world.conversation(S, 7, conv).title == "Erster Titel"
        assert len(titles.seen) == 1

    async def test_a_title_set_by_the_user_meanwhile_wins(self, world: Store) -> None:  # noqa: F811
        release = asyncio.Event()

        class Slow(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                await release.wait()
                return await super()._agenerate(*args, **kwargs)

        svc = titling_service(
            world, ScriptedModel(script=[AIMessage(content="ok")]), Slow(script=[titled("KI")])
        )
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="Frage")
        world.set_conversation_title(S, 7, conv, "Mein Name")
        release.set()
        await wait_done(svc, conv)
        await settle(svc)
        assert world.conversation(S, 7, conv).title == "Mein Name"
        assert "title" not in kinds(svc.replay(v, conv, after_seq=0))

    async def test_failure_keeps_the_first_message_as_title(self, world: Store) -> None:  # noqa: F811
        def boom(messages: list[BaseMessage]) -> AIMessage:
            raise RuntimeError("utility down")

        svc = titling_service(
            world, ScriptedModel(script=[AIMessage(content="ok")]), ScriptedModel(script=[boom])
        )
        v = verified(world)
        conv, turn = await svc.send(v, conversation_id=None, course_id=None, text="Wo ist B204?")
        await wait_done(svc, conv)
        await settle(svc)
        assert world.conversation(S, 7, conv).title == "Wo ist B204?"
        assert world.turn(conv, turn).state == "done"
        assert "title" not in kinds(svc.replay(v, conv, after_seq=0))

    async def test_a_slow_title_does_not_hold_the_answer_forever(
        self,
        world: Store,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import moodle_llm_chat.chat.service as chat_service

        monkeypatch.setattr(chat_service, "TITLE_WAIT_S", 0.05)
        release = asyncio.Event()

        class Slow(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                await release.wait()
                return await super()._agenerate(*args, **kwargs)

        svc = titling_service(
            world, ScriptedModel(script=[AIMessage(content="ok")]), Slow(script=[titled("Spät")])
        )
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="Frage")
        await wait_done(svc, conv)
        assert "final" in kinds(svc.replay(v, conv, after_seq=0))
        release.set()
        await settle(svc)
        events = svc.replay(v, conv, after_seq=0)
        assert kinds(events)[-1] == "title"
        assert world.conversation(S, 7, conv).title == "Spät"

    async def test_without_a_title_model_the_first_message_is_the_title(
        self,
        world: Store,  # noqa: F811
    ) -> None:
        svc = service(world, ScriptedModel(script=[AIMessage(content="ok")]))
        v = verified(world)
        conv, _ = await svc.send(v, conversation_id=None, course_id=None, text="x" * 100)
        await wait_done(svc, conv)
        assert world.conversation(S, 7, conv).title == "x" * TITLE_CHARS

    async def test_shutdown_cancels_pending_titles(self, world: Store) -> None:  # noqa: F811
        class Hang(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                await asyncio.sleep(60)

        class HangChat(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                await asyncio.sleep(60)

        svc = titling_service(world, HangChat(), Hang())
        await svc.send(verified(world), conversation_id=None, course_id=None, text="Frage")
        await asyncio.sleep(0.01)
        await asyncio.wait_for(svc.shutdown(), 2)
        assert not svc._title_tasks


def test_title_event_shape() -> None:
    event = TitleEvent(turn_id="t", title="Raum").model_dump(mode="json")
    assert event["type"] == "title" and event["title"] == "Raum"
