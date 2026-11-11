"""compact_events: stored streaming deltas folded into one event per text run."""

from __future__ import annotations

from typing import Any

from moodle_llm_chat.chat.history import compact_events


def ev(seq: int, type_: str, turn: str = "t1", **fields: Any) -> dict[str, Any]:
    return {"v": 1, "seq": seq, "turn_id": turn, "type": type_, **fields}


class TestCompactEvents:
    def test_empty(self) -> None:
        assert compact_events([]) == []

    def test_token_run_becomes_one_event_with_the_last_seq(self) -> None:
        events = [
            ev(1, "token", delta="Hal"),
            ev(2, "token", delta="lo"),
            ev(3, "token", delta="!"),
        ]
        assert compact_events(events) == [ev(3, "token", delta="Hallo!")]

    def test_thinking_run_is_merged_too(self) -> None:
        events = [ev(1, "thinking", delta="a"), ev(2, "thinking", delta="b")]
        assert compact_events(events) == [ev(2, "thinking", delta="ab")]

    def test_other_events_split_runs_and_pass_through_unchanged(self) -> None:
        call = ev(3, "tool_call", id="c1", name="search_text", args={"query": "x"})
        events = [
            ev(1, "thinking", delta="hm"),
            ev(2, "token", delta="A"),
            call,
            ev(4, "token", delta="B"),
            ev(5, "token", delta="C"),
            ev(6, "final", text="ABC", citations=[]),
        ]
        assert compact_events(events) == [
            ev(1, "thinking", delta="hm"),
            ev(2, "token", delta="A"),
            call,
            ev(5, "token", delta="BC"),
            ev(6, "final", text="ABC", citations=[]),
        ]

    def test_runs_do_not_cross_turns(self) -> None:
        events = [ev(1, "token", "t1", delta="a"), ev(2, "token", "t2", delta="b")]
        assert compact_events(events) == events

    def test_input_is_not_mutated(self) -> None:
        events = [ev(1, "token", delta="a"), ev(2, "token", delta="b")]
        compact_events(events)
        assert events == [ev(1, "token", delta="a"), ev(2, "token", delta="b")]

    def test_missing_delta_counts_as_empty(self) -> None:
        events = [ev(1, "token"), ev(2, "token", delta="b")]
        assert compact_events(events) == [ev(2, "token", delta="b")]
