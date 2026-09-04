"""Verifies spec 009 — Matrix bot behaviour.

The nio client is faked: these tests are about *policy* (who gets answered, in what
form, how often), which is where the bugs that annoy a real class live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

import pytest

from bsbot.matrix.bot import BerufsschuleBot, BotPolicy
from bsbot.rag.pipeline import Answer, Citation

ROOM = "!klasse:example.org"
BOT_ID = "@bsbot:example.org"
USER = "@max:example.org"
START = 1_000_000


@dataclass
class FakeEvent:
    body: str
    sender: str = USER
    server_timestamp: int = START + 1000
    event_id: str = "$evt1"
    formatted_body: str | None = None
    source: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakePowerLevels:
    users: dict[str, int] = field(default_factory=dict)
    users_default: int = 0

    def get_user_level(self, user_id: str) -> int:
        return self.users.get(user_id, self.users_default)


@dataclass
class FakeRoom:
    room_id: str = ROOM
    display_name: str = "IT4"
    power_levels: Any = None


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.typing: list[bool] = []
        self.user_id = BOT_ID

    async def room_send(self, room_id: str, message_type: str, content: dict[str, Any], **kw):
        self.sent.append({"room_id": room_id, "content": content})
        return None

    async def room_typing(self, room_id: str, typing_state: bool, timeout: int = 30000):
        self.typing.append(typing_state)
        return None


class FakePipeline:
    def __init__(self, answer: Answer | None = None, raises: bool = False) -> None:
        self._answer = answer or Answer(text="Antwort.", grounded=True)
        self._raises = raises
        self.questions: list[str] = []
        self.histories: list[list[tuple[str, str]] | None] = []

    def answer(
        self,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        room_id: str | None = None,
    ) -> Answer:
        self.questions.append(question)
        self.histories.append(history)
        if self._raises:
            raise RuntimeError("pipeline exploded")
        return self._answer


def make_bot(
    pipeline: FakePipeline | None = None,
    store: Any | None = None,
    embedder: Any | None = None,
    now: Any | None = None,
    **policy_kw,
) -> tuple[BerufsschuleBot, FakeClient, FakePipeline]:
    client = FakeClient()
    pipeline = pipeline or FakePipeline()
    policy = BotPolicy(
        room_ids={ROOM}, user_id=BOT_ID, display_name="bsbot", started_at_ms=START, **policy_kw
    )
    bot = BerufsschuleBot(client, pipeline, policy, store=store, embedder=embedder, now=now)  # type: ignore[arg-type]
    return bot, client, pipeline


class TestScope:
    async def test_ignores_other_rooms(self) -> None:
        """AC-1"""
        bot, client, pipeline = make_bot()
        await bot.handle_message(FakeRoom(room_id="!anderer:example.org"), FakeEvent("!bs Frage?"))
        assert pipeline.questions == [] and client.sent == []

    async def test_ignores_its_own_messages(self) -> None:
        """AC-2: the fatal loop."""
        bot, _, pipeline = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?", sender=BOT_ID))
        assert pipeline.questions == []

    async def test_ignores_messages_from_before_startup(self) -> None:
        """AC-3: a restart must not re-answer the whole backlog."""
        bot, _, pipeline = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Alt?", server_timestamp=START - 1))
        assert pipeline.questions == []


class TestTriggering:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ("!bs Wann ist die Prüfung?", "Wann ist die Prüfung?"),
            ("!bs    Wann?", "Wann?"),
            ("bsbot: Wann ist die Prüfung?", "Wann ist die Prüfung?"),
            ("bsbot Wann ist die Prüfung?", "Wann ist die Prüfung?"),
            (f"{BOT_ID}: Wann ist die Prüfung?", "Wann ist die Prüfung?"),
        ],
    )
    async def test_trigger_forms_and_prefix_stripping(self, body: str, expected: str) -> None:
        """AC-4 / AC-6"""
        bot, _, pipeline = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent(body))
        assert pipeline.questions == [expected]

    async def test_untargeted_message_is_ignored_by_default(self) -> None:
        """AC-4: the bot must be quiet in a shared class room."""
        bot, _, pipeline = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("Wann ist eigentlich die Prüfung?"))
        assert pipeline.questions == []

    async def test_reply_to_the_bot_counts_as_addressing_it(self) -> None:
        """AC-4"""
        bot, _, pipeline = make_bot()
        event = FakeEvent(
            "Und wo genau?",
            source={"content": {"m.relates_to": {"m.in_reply_to": {"event_id": "$bot-msg"}}}},
        )
        bot.remember_own_message("$bot-msg")
        await bot.handle_message(FakeRoom(), event)
        assert pipeline.questions == ["Und wo genau?"]

    async def test_answer_all_mode_responds_to_questions(self) -> None:
        """AC-5"""
        bot, _, pipeline = make_bot(answer_all=True)
        await bot.handle_message(FakeRoom(), FakeEvent("Wann ist die Prüfung?"))
        assert pipeline.questions == ["Wann ist die Prüfung?"]

    async def test_answer_all_still_ignores_statements(self) -> None:
        """AC-5"""
        bot, _, pipeline = make_bot(answer_all=True)
        await bot.handle_message(FakeRoom(), FakeEvent("Bis morgen Leute"))
        assert pipeline.questions == []

    async def test_empty_question_after_prefix_is_ignored(self) -> None:
        bot, _, pipeline = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("!bs    "))
        assert pipeline.questions == []


class TestReplying:
    async def test_reply_is_threaded(self) -> None:
        """AC-7: keep the class timeline readable."""
        bot, client, _ = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?", event_id="$q1"))
        relates = client.sent[0]["content"]["m.relates_to"]
        assert relates["rel_type"] == "m.thread"
        assert relates["event_id"] == "$q1"

    async def test_citations_render_as_links(self) -> None:
        """AC-8"""
        answer = Answer(
            text="Die Prüfung ist am 15.03. [1]",
            grounded=True,
            citations=[
                Citation(
                    index=1,
                    title="Merkblatt",
                    course_name="SuK",
                    header_text="SuK › Merkblatt",
                    url="https://moodle.example.de/mod/resource/view.php?id=7",
                    page=3,
                )
            ],
        )
        bot, client, _ = make_bot(FakePipeline(answer))
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?"))
        content = client.sent[0]["content"]
        assert content["format"] == "org.matrix.custom.html"
        assert "https://moodle.example.de/mod/resource/view.php?id=7" in content["formatted_body"]
        assert "<a href" in content["formatted_body"]
        assert "S. 3" in content["formatted_body"]
        assert "<a href" not in content["body"]

    async def test_typing_indicator_is_set_and_cleared(self) -> None:
        """AC-9"""
        bot, client, _ = make_bot()
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?"))
        assert client.typing == [True, False]

    async def test_typing_is_cleared_even_when_the_pipeline_raises(self) -> None:
        """AC-9 / AC-10: a stuck 'bsbot is typing…' looks broken forever."""
        bot, client, _ = make_bot(FakePipeline(raises=True))
        await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?"))
        assert client.typing == [True, False]
        assert client.sent, "an apology should still be sent"
        assert "Fehler" in client.sent[0]["content"]["body"]


class TestPoliteness:
    async def test_rate_limited_user_gets_one_notice(self) -> None:
        """AC-13: never reply-per-message to a flooding user."""
        bot, client, pipeline = make_bot(max_per_user_per_minute=2)
        for i in range(5):
            await bot.handle_message(FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}"))
        assert len(pipeline.questions) == 2
        notices = [s for s in client.sent if "zu viele" in s["content"]["body"].lower()]
        assert len(notices) == 1

    async def test_rate_limit_is_per_user(self) -> None:
        """AC-13"""
        bot, _, pipeline = make_bot(max_per_user_per_minute=1)
        await bot.handle_message(FakeRoom(), FakeEvent("!bs A?", sender="@a:example.org"))
        await bot.handle_message(FakeRoom(), FakeEvent("!bs B?", sender="@b:example.org"))
        assert len(pipeline.questions) == 2

    async def test_daily_quota_blocks_after_default_five(self) -> None:
        """AC-27: default daily quota is 5, independent of the burst limit."""
        bot, client, pipeline = make_bot(max_per_user_per_minute=100)
        for i in range(7):
            await bot.handle_message(FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}"))
        assert len(pipeline.questions) == 5
        notices = [s for s in client.sent if "limit" in s["content"]["body"].lower()]
        assert len(notices) == 1

    async def test_daily_quota_is_configurable(self) -> None:
        """AC-27"""
        bot, _, pipeline = make_bot(max_per_user_per_minute=100, max_per_user_per_day=2)
        for i in range(4):
            await bot.handle_message(FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}"))
        assert len(pipeline.questions) == 2

    async def test_daily_quota_is_per_user(self) -> None:
        """AC-27"""
        bot, _, pipeline = make_bot(max_per_user_per_minute=100, max_per_user_per_day=1)
        await bot.handle_message(FakeRoom(), FakeEvent("!bs A?", sender="@a:example.org"))
        await bot.handle_message(FakeRoom(), FakeEvent("!bs B?", sender="@b:example.org"))
        assert len(pipeline.questions) == 2

    async def test_daily_quota_resets_on_a_new_day(self) -> None:
        """AC-27: the quota is per calendar day (UTC), not a rolling 24h window."""
        from datetime import datetime

        day1 = datetime(2026, 1, 1, 23, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 2, 0, 30, tzinfo=UTC)
        clock = {"now": day1}
        bot, _, pipeline = make_bot(
            max_per_user_per_minute=100, max_per_user_per_day=1, now=lambda: clock["now"]
        )
        await bot.handle_message(FakeRoom(), FakeEvent("!bs A?", event_id="$e1"))
        clock["now"] = day2
        await bot.handle_message(FakeRoom(), FakeEvent("!bs B?", event_id="$e2"))
        assert len(pipeline.questions) == 2

    async def test_bypass_user_is_exempt_from_burst_and_daily_limits(self) -> None:
        """AC-28"""
        bot, client, pipeline = make_bot(
            max_per_user_per_minute=1,
            max_per_user_per_day=1,
            rate_limit_bypass_users=frozenset({USER}),
        )
        for i in range(5):
            await bot.handle_message(FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}"))
        assert len(pipeline.questions) == 5
        assert client.sent == [] or all(
            "limit" not in s["content"]["body"].lower()
            and "zu viele" not in s["content"]["body"].lower()
            for s in client.sent
        )

    async def test_burst_and_daily_notices_are_worded_differently(self) -> None:
        """AC-29"""
        burst_bot, burst_client, _ = make_bot(max_per_user_per_minute=1, max_per_user_per_day=100)
        for i in range(2):
            await burst_bot.handle_message(
                FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}")
            )
        burst_notice = burst_client.sent[-1]["content"]["body"]

        daily_bot, daily_client, _ = make_bot(max_per_user_per_minute=100, max_per_user_per_day=1)
        for i in range(2):
            await daily_bot.handle_message(
                FakeRoom(), FakeEvent(f"!bs Frage {i}?", event_id=f"$e{i}")
            )
        daily_notice = daily_client.sent[-1]["content"]["body"]

        assert burst_notice != daily_notice

    async def test_non_text_events_are_ignored(self) -> None:
        """AC-14"""
        bot, _, pipeline = make_bot()

        class ImageEvent(FakeEvent):
            pass

        await bot.handle_message(FakeRoom(), ImageEvent("!bs Frage?"), msgtype="m.image")
        assert pipeline.questions == []


class TestStructuredMentions:
    """Spec 009 AC-4 (structured mentions), regression from live use.

    Element's @-autocomplete inserts a pill anywhere in the message and marks the
    event with MSC3952 ``m.mentions.user_ids`` — it does not reliably prepend the
    bot's display name as plain text at the start of ``body``. The bot must honour
    the structured field, not just the fragile text-prefix heuristic.
    """

    async def test_structured_mention_anywhere_in_the_message_triggers_a_reply(self) -> None:
        bot, _, pipeline = make_bot()
        event = FakeEvent(
            "Hey Bsbot, wann ist die Prüfung?",
            source={"content": {"m.mentions": {"user_ids": [BOT_ID]}}},
        )
        await bot.handle_message(FakeRoom(), event)
        assert pipeline.questions == ["Hey Bsbot, wann ist die Prüfung?"]

    async def test_mentioning_someone_else_is_not_a_trigger(self) -> None:
        bot, _, pipeline = make_bot()
        event = FakeEvent(
            "Hey @max, wann ist die Prüfung?",
            source={"content": {"m.mentions": {"user_ids": ["@max:example.org"]}}},
        )
        await bot.handle_message(FakeRoom(), event)
        assert pipeline.questions == []

    async def test_empty_mentions_list_is_not_a_trigger(self) -> None:
        bot, _, pipeline = make_bot()
        event = FakeEvent(
            "Just talking about the exam.",
            source={"content": {"m.mentions": {"user_ids": []}}},
        )
        await bot.handle_message(FakeRoom(), event)
        assert pipeline.questions == []


class TestTransparencyLogging:
    """Room/sender-level visibility: who triggered the bot, and whether a reply
    actually went out — the layer above the question/retrieval/answer detail that
    rag.pipeline already logs.
    """

    async def test_logs_who_triggered_it(self) -> None:
        import structlog.testing

        bot, _, _ = make_bot()
        with structlog.testing.capture_logs() as logs:
            await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?"))
        events = [e for e in logs if e.get("event") == "bot.triggered"]
        assert events and events[0]["room"] == ROOM and events[0]["sender"] == USER

    async def test_logs_a_successful_reply(self) -> None:
        import structlog.testing

        bot, _, _ = make_bot()
        with structlog.testing.capture_logs() as logs:
            await bot.handle_message(FakeRoom(), FakeEvent("!bs Frage?"))
        events = [e for e in logs if e.get("event") == "bot.replied"]
        assert events and events[0]["grounded"] is True

    async def test_ignored_messages_produce_no_trigger_log(self) -> None:
        import structlog.testing

        bot, _, _ = make_bot()
        with structlog.testing.capture_logs() as logs:
            await bot.handle_message(FakeRoom(), FakeEvent("Just chatting, no trigger here"))
        assert not [e for e in logs if e.get("event") == "bot.triggered"]


class TestConversationTurns:
    async def test_reply_to_bot_message_passes_conversation_history(self) -> None:
        pipeline = FakePipeline(Answer(text="Die Klausur ist am 15. März.", grounded=True))
        bot, client, pipeline = make_bot(pipeline=pipeline)

        # First turn: bot answers and remembers its sent event
        first_event = FakeEvent("!bs Wann ist die Klausur?", event_id="$first_q")

        # Configure client response for first message
        class FakeResponse:
            event_id = "$bot_reply_1"

        async def fake_room_send(room_id, msg_type, content, **kw):
            return FakeResponse()

        client.room_send = fake_room_send  # type: ignore[assignment]

        await bot.handle_message(FakeRoom(), first_event)
        assert pipeline.questions == ["Wann ist die Klausur?"]
        assert pipeline.histories == [None]

        # Second turn: student replies to bot's reply event $bot_reply_1
        second_event = FakeEvent(
            "Und wo findet sie statt?",
            event_id="$second_q",
            source={"content": {"m.relates_to": {"m.in_reply_to": {"event_id": "$bot_reply_1"}}}},
        )
        await bot.handle_message(FakeRoom(), second_event)
        assert pipeline.questions == ["Wann ist die Klausur?", "Und wo findet sie statt?"]
        assert pipeline.histories[1] == [("Wann ist die Klausur?", "Die Klausur ist am 15. März.")]

    async def test_reply_within_an_existing_thread_stays_in_that_thread(self) -> None:
        """AC-7 regression: a student replying a second time inside the thread the
        bot already started must get an answer threaded to the *same* root — not a
        brand new thread rooted at their reply, which would leave the answer
        disconnected from the conversation the student is actually looking at.
        """
        bot, client, pipeline = make_bot()

        # Student's reply already lives inside the thread the bot rooted at "$q1":
        # per MSC3440 the root event_id never changes turn to turn, only
        # m.in_reply_to (the fallback) advances to the latest event in the thread.
        second_event = FakeEvent(
            "Und wo findet sie statt?",
            event_id="$q2",
            source={
                "content": {
                    "m.relates_to": {
                        "rel_type": "m.thread",
                        "event_id": "$q1",
                        "is_falling_back": True,
                        "m.in_reply_to": {"event_id": "$bot_reply_1"},
                    }
                }
            },
        )
        bot.remember_own_message("$bot_reply_1", turn=("Wann ist die Klausur?", "Antwort."))

        await bot.handle_message(FakeRoom(), second_event)

        assert pipeline.questions == ["Und wo findet sie statt?"]
        relates = client.sent[0]["content"]["m.relates_to"]
        assert relates["event_id"] == "$q1"
        assert relates["m.in_reply_to"]["event_id"] == "$q1"


class FakeStore:
    def __init__(self) -> None:
        self.indexed: list[dict[str, Any]] = []

    def index_matrix_message(
        self,
        room_id: str,
        event_id: str,
        sender: str,
        text: str,
        timemodified: int,
        *,
        room_name: str = "",
        max_history_per_room: int = 10,
        embedder: Any = None,
        now: int | None = None,
    ) -> list[int]:
        self.indexed.append(
            {
                "room_id": room_id,
                "event_id": event_id,
                "sender": sender,
                "text": text,
                "timemodified": timemodified,
                "room_name": room_name,
                "max_history_per_room": max_history_per_room,
            }
        )
        return [1]


class TestModeratorMessageIngestion:
    async def test_moderator_message_is_indexed_and_embedded(self) -> None:
        store = FakeStore()
        bot, _, _ = make_bot(store=store)
        room = FakeRoom(
            room_id=ROOM,
            display_name="IT4",
            power_levels=FakePowerLevels(users={"@teacher:example.org": 50}),
        )
        event = FakeEvent(
            "Wichtige Ankündigung: Am Freitag kein Unterricht.",
            sender="@teacher:example.org",
            event_id="$mod_msg_1",
            server_timestamp=1725187200000,
        )
        await bot.handle_message(room, event)

        assert len(store.indexed) == 1
        entry = store.indexed[0]
        assert entry["room_id"] == ROOM
        assert entry["event_id"] == "$mod_msg_1"
        assert entry["sender"] == "@teacher:example.org"
        assert entry["text"] == "Wichtige Ankündigung: Am Freitag kein Unterricht."
        assert entry["timemodified"] == 1725187200
        assert entry["room_name"] == "IT4"
        assert entry["max_history_per_room"] == 10

    async def test_normal_user_message_is_not_indexed(self) -> None:
        store = FakeStore()
        bot, _, _ = make_bot(store=store)
        room = FakeRoom(
            room_id=ROOM,
            display_name="IT4",
            power_levels=FakePowerLevels(users={"@student:example.org": 0}),
        )
        event = FakeEvent(
            "Wer hat die Hausaufgaben?",
            sender="@student:example.org",
            event_id="$student_msg_1",
        )
        await bot.handle_message(room, event)
        assert store.indexed == []

    async def test_bot_command_by_moderator_is_not_indexed_as_announcement(self) -> None:
        store = FakeStore()
        bot, _, pipeline = make_bot(store=store)
        room = FakeRoom(
            room_id=ROOM,
            display_name="IT4",
            power_levels=FakePowerLevels(users={"@teacher:example.org": 50}),
        )
        event = FakeEvent(
            "!bs Wann beginnt das Praktikum?",
            sender="@teacher:example.org",
            event_id="$mod_cmd_1",
        )
        await bot.handle_message(room, event)
        # It should be answered by the bot, not indexed as a moderator knowledge announcement
        assert store.indexed == []
        assert pipeline.questions == ["Wann beginnt das Praktikum?"]
