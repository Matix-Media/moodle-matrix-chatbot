"""Syncs, restarts and interruptions: nothing is lost, nothing is wrongly deleted."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.db import DatabaseKeyError
from moodle_llm_chat.index.store import Store
from support import ScriptedModel, call, pdf_bytes
from support.harness import MOODLE, Api, document, file_document, ws_until

USER = 7
SCOPE = {"cmids": {10, 11, 12}, "courses": {2}}


def socket(api: Api) -> Any:
    ws = api.http.websocket_connect("wss://testserver/ws/chat", headers={"origin": MOODLE})
    return ws


def auth(api: Api, ws: Any) -> None:
    ws.send_json({"type": "auth", "token": api.token(USER, **SCOPE)})
    assert ws.receive_json()["type"] == "ready"


class TestFullSync:
    def test_manifest_sync_tombstones_only_what_disappeared(self, api: Api) -> None:
        with api.running():
            api.push_documents(document("2:10:a", text="Alpha"), document("2:11:b", text="Beta"))
            api.idle()
            h = api.site_headers
            assert (
                api.http.post(
                    "/ingest/sync/start", json={"sync_id": "n1", "mode": "full"}, headers=h
                ).status_code
                == 200
            )
            entries = {"2:10:a": document("2:10:a", text="Alpha")["content_hash"], "2:12:c": "new"}
            need = api.http.post(
                "/ingest/manifest", json={"sync_id": "n1", "entries": entries}, headers=h
            ).json()["need"]
            assert need == ["2:12:c"]
            api.push_documents(document("2:12:c", text="Gamma"), sync_id="n1")
            result = api.http.post("/ingest/sync/finish", json={"sync_id": "n1"}, headers=h).json()
            assert result["tombstoned"] == ["2:11:b"]
            api.idle()
            store = api.store
            assert api.on_loop(lambda: store.fts_chunk_ids("schule", '"Beta"', limit=3)) == []
            assert api.on_loop(lambda: store.fts_chunk_ids("schule", '"Gamma"', limit=3))

    def test_aborted_sync_never_deletes_anything(self, api: Api) -> None:
        with api.running():
            api.push_documents(document("2:10:a", text="Alpha"), document("2:11:b", text="Beta"))
            h = api.site_headers
            api.http.post("/ingest/sync/start", json={"sync_id": "n1", "mode": "full"}, headers=h)
            api.http.post("/ingest/manifest", json={"sync_id": "n1", "entries": {}}, headers=h)
            # The plugin's cron dies here; nothing calls finish. A new sync starts later.
            api.http.post(
                "/ingest/sync/start", json={"sync_id": "n2", "mode": "incremental"}, headers=h
            )
            api.http.post("/ingest/sync/finish", json={"sync_id": "n2"}, headers=h)
            assert api.http.get("/ingest/status", headers=h).json()["documents"] == 2

    def test_orphaned_file_is_deleted_after_full_sync(self, api: Api) -> None:
        data = pdf_bytes(["Altes Skript mit ausreichend Text fuer den Index. " * 3])
        with api.running():
            api.push_documents(file_document("2:10:f", data, "alt.pdf"))
            api.upload(data)
            api.idle()
            h = api.site_headers
            api.http.post("/ingest/sync/start", json={"sync_id": "n1", "mode": "full"}, headers=h)
            api.http.post("/ingest/sync/finish", json={"sync_id": "n1"}, headers=h)
        assert not list((api.data_dir / "blobs").rglob("*.enc"))


class TestRestart:
    def test_everything_survives_a_restart(self, api: Api) -> None:
        api.model.script.extend([AIMessage(content="Erste Antwort.")])
        with api.running():
            api.push_documents(document("2:10:a", text="Alpha"))
            api.idle()
            with socket(api) as ws:
                auth(api, ws)
                ws.send_json(
                    {"type": "send", "text": "Frage eins", "token": api.token(USER, **SCOPE)}
                )
                conv = ws_until(ws, "quota")[0]["conversation_id"]
        with api.running():
            bearer = {"Authorization": f"Bearer {api.token(USER, **SCOPE)}"}
            listed = api.http.get("/api/conversations", headers=bearer).json()
            assert [c["id"] for c in listed] == [conv]
            events = api.http.get(f"/api/conversations/{conv}", headers=bearer).json()["events"]
            assert any(e["type"] == "final" and e["text"] == "Erste Antwort." for e in events)
            assert api.http.get("/ingest/status", headers=api.site_headers).json()["documents"] == 1

    def test_file_waiting_for_its_upload_is_processed_after_a_restart(self, api: Api) -> None:
        data = pdf_bytes(["Nachgereichtes Skript ueber Spanning Tree mit genug Text. " * 2])
        with api.running():
            api.push_documents(file_document("2:10:f", data, "stp.pdf"))
            api.idle()
        with api.running():
            api.upload(data)
            api.idle()
            store = api.store
            assert api.on_loop(lambda: store.fts_chunk_ids("schule", '"Spanning"', limit=3))

    def test_open_question_can_be_answered_after_a_restart(self, api: Api) -> None:
        api.model.script.extend(
            [call("ask_user", {"question": "Welcher Kurs?", "options": ["LF7", "MA"]})]
        )
        with api.running(), socket(api) as ws:
            auth(api, ws)
            ws.send_json(
                {
                    "type": "send",
                    "text": "Wann ist die Prüfung?",
                    "token": api.token(USER, **SCOPE),
                }
            )
            frames = ws_until(ws, "question")
            conv, question = frames[0]["conversation_id"], frames[-1]
        api.model.script.extend([AIMessage(content="In LF7 am Freitag.")])
        with api.running(), socket(api) as ws:
            auth(api, ws)
            ws.send_json({"type": "resume", "conversation_id": conv, "last_seq": question["seq"]})
            ws.send_json(
                {
                    "type": "answer",
                    "conversation_id": conv,
                    "interrupt_id": question["interrupt_id"],
                    "response": {"choices": ["LF7"]},
                    "token": api.token(USER, **SCOPE),
                }
            )
            final = ws_until(ws, "final")[-1]
        assert final["text"] == "In LF7 am Freitag."
        # The model saw its own question and the user's choice from the restored checkpoint.
        assert "LF7" in str(api.model.seen[-1][-1].content)

    def test_turn_cut_off_by_shutdown_can_be_continued(self, api: Api) -> None:
        started = asyncio.Event()

        class Hanging(ScriptedModel):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                started.set()
                await asyncio.sleep(60)

        api.model = Hanging()
        with api.running(), socket(api) as ws:
            auth(api, ws)
            ws.send_json({"type": "send", "text": "Lange Frage", "token": api.token(USER, **SCOPE)})
            frames = ws_until(ws, "user_message")
            conv, turn = frames[0]["conversation_id"], frames[0]["turn_id"]
            api.on_loop(lambda: asyncio.wait_for(started.wait(), 5))
        # Shutdown cancelled the turn mid-call.
        api.model = ScriptedModel(script=[AIMessage(content="Nach dem Neustart beantwortet.")])
        with api.running(), socket(api) as ws:
            auth(api, ws)
            ws.send_json({"type": "resume", "conversation_id": conv, "last_seq": 0})
            replay = ws_until(ws, "turn_interrupted")
            assert replay[-1]["turn_id"] == turn
            ws.send_json(
                {
                    "type": "continue",
                    "conversation_id": conv,
                    "turn_id": turn,
                    "token": api.token(USER, **SCOPE),
                }
            )
            final = ws_until(ws, "final")[-1]
        assert final["text"] == "Nach dem Neustart beantwortet."
        # The interrupted user message was not duplicated in the model's history.
        humans = [m for m in api.model.seen[-1] if m.type == "human"]
        assert [str(m.content) for m in humans] == ["Lange Frage"]


class TestKeys:
    def test_wrong_master_key_cannot_open_the_data(self, api: Api) -> None:
        with api.running():
            api.push_documents(document("2:10:a", text="Alpha"))
        with pytest.raises(DatabaseKeyError):
            Store(api.data_dir / "index.db", KeyRing(os.urandom(32)), embed_dim=64).open()

    def test_rotated_key_keeps_everything_readable(self, api: Api) -> None:
        api.model.script.extend([AIMessage(content="Vor der Rotation.")])
        with api.running():
            api.push_documents(document("2:10:a", text="Alpha"))
            api.idle()
            with socket(api) as ws:
                auth(api, ws)
                ws.send_json({"type": "send", "text": "x", "token": api.token(USER, **SCOPE)})
                conv = ws_until(ws, "quota")[0]["conversation_id"]
        new_master = os.urandom(32)
        with Store(api.data_dir / "index.db", KeyRing(api.master), embed_dim=64) as store:
            store.rotate_master_key(KeyRing(new_master))
        api.master = new_master
        with api.running():
            bearer = {"Authorization": f"Bearer {api.token(USER, **SCOPE)}"}
            events = api.http.get(f"/api/conversations/{conv}", headers=bearer).json()["events"]
            assert any(e.get("text") == "Vor der Rotation." for e in events)
            store = api.store
            assert api.on_loop(lambda: store.fts_chunk_ids("schule", '"Alpha"', limit=3))
