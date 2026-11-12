"""Server-side chat history and the encrypted LangGraph checkpointer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from factories import read_disk
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from moodle_llm_chat.agent.checkpoint import EncryptedCheckpointer, thread_id_for
from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.store import Store, StoreError

S = "site-a"


class TestConversations:
    def test_create_list_and_ownership(self, store: Store) -> None:
        a = store.create_conversation(S, 1, course_id=2)
        store.create_conversation(S, 2)
        assert [c.id for c in store.conversations(S, 1)] == [a]
        with pytest.raises(StoreError):
            store.conversation(S, 2, a)

    def test_title_is_encrypted(self, store: Store, tmp_path: Path) -> None:
        conv = store.create_conversation(S, 1)
        store.set_conversation_title(S, 1, conv, "Frage zur Abschlussprüfung")
        assert store.conversation(S, 1, conv).title == "Frage zur Abschlussprüfung"
        store.close()
        assert b"Abschlusspr" not in read_disk(tmp_path / "index.db")

    def test_events_append_with_sequence_and_replay_after(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        seqs = [store.append_event(S, 1, conv, "t1", {"type": "token", "delta": d}) for d in "abc"]
        assert seqs == [1, 2, 3]
        assert [e["delta"] for _, e in store.events(S, 1, conv, after_seq=1)] == ["b", "c"]

    def test_event_seqs_are_never_reused_after_a_restart(
        self, tmp_path: Path, ring: KeyRing
    ) -> None:
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            conv = store.create_conversation(S, 1)
            # Handed out to live subscribers, never stored (a crash lost them).
            assert [store.next_seq(conv) for _ in range(3)] == [1, 2, 3]
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            seq = store.append_event(S, 1, conv, "t1", {"type": "turn_interrupted"})
            assert seq > 3
            assert store.next_seq(conv) == seq + 1

    def test_append_event_keeps_a_given_seq(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        seqs = [store.next_seq(conv) for _ in range(3)]
        store.append_event(S, 1, conv, "t1", {"type": "token", "delta": "abc"}, seq=seqs[-1])
        assert [(seq, e["seq"]) for seq, e in store.events(S, 1, conv)] == [(3, 3)]

    def test_a_partly_seen_streamed_run_resumes_where_it_was_left(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        store.append_event(S, 1, conv, "t1", {"type": "user_message", "text": "?"})  # seq 1
        # One stored run of three deltas that went out live as seqs 2, 3 and 4.
        run = {"type": "token", "delta": "Hallo Welt!", "parts": [5, 5, 1]}
        store.append_event(S, 1, conv, "t1", run, seq=4)

        def tail(after: int) -> list[Any]:
            return [e.get("delta") for _, e in store.events(S, 1, conv, after_seq=after)]

        assert tail(1) == ["Hallo Welt!"]
        assert tail(2) == [" Welt!"]
        assert tail(3) == ["!"]
        assert tail(4) == []
        assert all("parts" not in e for _, e in store.events(S, 1, conv))

    def test_a_partly_seen_thinking_indicator_is_not_repeated(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        run = {"type": "thinking", "delta": None, "parts": [0, 0]}
        store.append_event(S, 1, conv, "t1", run, seq=2)
        assert [e["delta"] for _, e in store.events(S, 1, conv)] == [None]
        assert store.events(S, 1, conv, after_seq=1) == []

    def test_events_are_encrypted_and_owner_only(self, store: Store, tmp_path: Path) -> None:
        conv = store.create_conversation(S, 1)
        store.append_event(S, 1, conv, "t1", {"type": "user_message", "text": "Meine Note ist 5"})
        with pytest.raises(StoreError):
            store.events(S, 2, conv)
        store.close()
        assert b"Meine Note" not in read_disk(tmp_path / "index.db")

    def test_users_have_distinct_chat_keys(self, store: Store) -> None:
        store.create_conversation(S, 1)
        store.create_conversation(S, 2)
        store.append_event(S, 1, store.conversations(S, 1)[0].id, "t", {"x": 1})
        store.append_event(S, 2, store.conversations(S, 2)[0].id, "t", {"x": 1})
        chat_keys = [k for k in store.all_wrapped_keys() if k[0] == "chat"]
        assert {k[1] for k in chat_keys} == {f"{S}:1", f"{S}:2"}

    def test_archive_hides_conversation(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        store.archive_conversation(S, 1, conv)
        assert store.conversations(S, 1) == []

    def test_turn_lifecycle_and_startup_recovery(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        store.start_turn(S, 1, conv, "t1", client_msg_id="m1")
        assert store.turn_for_client_message(conv, "m1") == "t1"
        assert store.running_turn(conv) == "t1"
        store.set_turn_state("t1", "waiting")
        assert store.running_turn(conv) == "t1"  # waiting for the user still blocks a new turn
        store.set_turn_state("t1", "running")
        assert store.interrupt_running_turns() == [(S, 1, conv, "t1")]
        assert store.turn(conv, "t1").state == "interrupted"
        assert store.running_turn(conv) is None

    def test_citations_persist_per_conversation(self, store: Store) -> None:
        conv = store.create_conversation(S, 1)
        refs = [
            {
                "index": 1,
                "doc_id": "d",
                "title": "T",
                "course_name": "C",
                "path": "/p",
                "url": None,
                "page": 2,
            }
        ]
        store.save_citations(S, 1, conv, refs)
        assert store.citations(S, 1, conv) == refs


def _graph(saver: EncryptedCheckpointer) -> object:
    def respond(state: MessagesState) -> dict[str, list[AIMessage]]:
        return {"messages": [AIMessage(content=f"Echo: {state['messages'][-1].content}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=saver)


class TestCheckpointer:
    async def test_state_survives_a_restart(self, tmp_path: Path, ring: KeyRing) -> None:
        thread = thread_id_for(S, 1, "conv-1")
        config = {"configurable": {"thread_id": thread}}
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            graph = _graph(EncryptedCheckpointer(store))
            await graph.ainvoke({"messages": [HumanMessage(content="Geheimfrage")]}, config)  # type: ignore[attr-defined]
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            graph = _graph(EncryptedCheckpointer(store))
            state = await graph.aget_state(config)  # type: ignore[attr-defined]
            assert [m.content for m in state.values["messages"]] == [
                "Geheimfrage",
                "Echo: Geheimfrage",
            ]
        assert b"Geheimfrage" not in read_disk(tmp_path / "db")

    async def test_threads_of_other_users_use_other_keys(self, store: Store) -> None:
        saver = EncryptedCheckpointer(store)
        graph = _graph(saver)
        for user in (1, 2):
            config = {"configurable": {"thread_id": thread_id_for(S, user, "c")}}
            await graph.ainvoke({"messages": [HumanMessage(content=f"u{user}")]}, config)  # type: ignore[attr-defined]
        assert {k[1] for k in store.all_wrapped_keys() if k[0] == "chat"} == {f"{S}:1", f"{S}:2"}

    async def test_delete_thread(self, store: Store) -> None:
        saver = EncryptedCheckpointer(store)
        graph = _graph(saver)
        config = {"configurable": {"thread_id": thread_id_for(S, 1, "c")}}
        await graph.ainvoke({"messages": [HumanMessage(content="x")]}, config)  # type: ignore[attr-defined]
        saver.delete_thread(thread_id_for(S, 1, "c"))
        fresh = EncryptedCheckpointer(store)
        assert fresh.get_tuple(config) is None  # type: ignore[arg-type]

    async def test_prune_keeps_only_the_latest_checkpoint(
        self, tmp_path: Path, ring: KeyRing
    ) -> None:
        thread = thread_id_for(S, 1, "conv-1")
        config = {"configurable": {"thread_id": thread}}
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            saver = EncryptedCheckpointer(store)
            graph = _graph(saver)
            for text in ("eins", "zwei"):
                await graph.ainvoke({"messages": [HumanMessage(content=text)]}, config)  # type: ignore[attr-defined]
            before = store.ckpt_rows(thread)
            assert len(before[0]) > 1
            saver.prune([thread])
            storage, _, blobs = store.ckpt_rows(thread)
            assert len(storage) == 1
            assert len(blobs) < len(before[2])
            assert len(list(saver.list(config))) == 1  # type: ignore[arg-type]
        with Store(tmp_path / "db", ring, embed_dim=3) as store:
            graph = _graph(EncryptedCheckpointer(store))
            await graph.ainvoke({"messages": [HumanMessage(content="drei")]}, config)  # type: ignore[attr-defined]
            state = await graph.aget_state(config)  # type: ignore[attr-defined]
            assert [m.content for m in state.values["messages"]] == [
                "eins",
                "Echo: eins",
                "zwei",
                "Echo: zwei",
                "drei",
                "Echo: drei",
            ]

    def test_prune_of_an_unknown_thread_is_a_no_op(self, store: Store) -> None:
        EncryptedCheckpointer(store).prune([thread_id_for(S, 1, "nothing")])

    def test_malformed_thread_id_is_rejected(self, store: Store) -> None:
        saver = EncryptedCheckpointer(store)
        with pytest.raises(ValueError):
            saver.get_tuple({"configurable": {"thread_id": "no-colons"}})
