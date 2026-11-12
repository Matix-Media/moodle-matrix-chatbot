"""Websocket chat protocol, conversation REST API, quota policy and usage routes."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect
from test_agent import ScriptedModel, call
from test_tools import FakeMoodle

from moodle_llm_chat.access.tokens import issue_chat_token
from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.web.app import create_app

S = "site-a"
URL = "https://moodle.a.test"


class Env:
    def __init__(self, client: TestClient, secret: str, model: ScriptedModel) -> None:
        self.client = client
        self.secret = secret
        self.model = model
        self._n = 0

    def token(self, user: int = 7, **claims: Any) -> str:
        self._n += 1
        now = int(time.time())
        base: dict[str, Any] = {
            "iss": URL,
            "sub": str(user),
            "name": "Erika",
            "caps": ["read", "interact"],
            "courses": [2],
            "cmids": [10],
            "jti": f"j{self._n}-{user}-{now}",
            "iat": now,
            "exp": now + 600,
        }
        base.update(claims)
        return issue_chat_token(self.secret, S, base)

    def on_loop(self, fn: Callable[..., Any], *args: Any) -> Any:
        assert self.client.portal is not None
        return self.client.portal.call(fn, *args)

    @property
    def store(self) -> Store:
        store: Store = self.client.app.state.store  # type: ignore[attr-defined]
        return store


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("MLC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MLC_EMBEDDING__DIM", "3")
    model = ScriptedModel()
    app = create_app(
        Settings(),
        ring=KeyRing(os.urandom(32)),
        start_workers=False,
        chat_model=model,
        access=FakeMoodle(),
    )
    with TestClient(app, base_url="https://testserver") as client:
        secret = client.portal.call(lambda: app.state.store.add_site(S, name="A", url=URL))  # type: ignore[union-attr]
        yield Env(client, secret, model)


def connect(env: Env, **kw: Any) -> Any:
    return env.client.websocket_connect("wss://testserver/ws/chat", **kw)


def until(ws: Any, kind: str, limit: int = 50) -> list[dict[str, Any]]:
    frames = []
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == kind:
            return frames
    raise AssertionError(f"no {kind!r} frame in {frames}")


def authed(env: Env, ws: Any, user: int = 7) -> dict[str, Any]:
    ws.send_json({"type": "auth", "token": env.token(user)})
    ready = ws.receive_json()
    assert ready["type"] == "ready", ready
    return ready


class TestAuth:
    def test_first_frame_must_be_auth(self, env: Env) -> None:
        with connect(env) as ws:
            ws.send_json({"type": "send", "text": "hi"})
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_json()
            assert err.value.code == 4401

    def test_bad_token(self, env: Env) -> None:
        with connect(env) as ws:
            ws.send_json({"type": "auth", "token": "garbage"})
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_json()
            assert err.value.code == 4401

    def test_wrong_origin(self, env: Env) -> None:
        with connect(env, headers={"origin": "https://evil.test"}) as ws:
            ws.send_json({"type": "auth", "token": env.token()})
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_json()
            assert err.value.code == 4403

    def test_ready_lists_tools_for_the_ui(self, env: Env) -> None:
        with connect(env, headers={"origin": URL}) as ws:
            ready = authed(env, ws)
            names = {t["name"] for t in ready["tools"]}
            assert "search_content" in names and "ask_user" in names
            assert ready["v"] == 1

    def test_plain_ws_refused_when_tls_required(self, env: Env) -> None:
        with env.client.websocket_connect("ws://testserver/ws/chat") as ws:
            with pytest.raises(WebSocketDisconnect) as err:
                ws.receive_json()
            assert err.value.code == 4400

    def test_token_reuse_on_send_is_refused(self, env: Env) -> None:
        with connect(env) as ws:
            authed(env, ws)
            token = env.token()
            env.model.script.extend([AIMessage(content="eins")])
            ws.send_json({"type": "send", "text": "a", "token": token})
            until(ws, "final")
            ws.send_json({"type": "send", "text": "b", "token": token})
            error = until(ws, "error")[-1]
            assert error["code"] == "unauthorized"

    def test_send_token_must_belong_to_the_same_user(self, env: Env) -> None:
        with connect(env) as ws:
            authed(env, ws, user=7)
            ws.send_json({"type": "send", "text": "a", "token": env.token(user=8)})
            assert until(ws, "error")[-1]["code"] == "auth_mismatch"


class TestChat:
    def test_full_turn_streams_events(self, env: Env) -> None:
        env.model.script.extend(
            [call("current_datetime", {}), AIMessage(content="Heute ist Dienstag.")]
        )
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json(
                {
                    "type": "send",
                    "text": "Welcher Tag?",
                    "token": env.token(),
                    "client_msg_id": "m1",
                }
            )
            frames = until(ws, "quota")
            types = [f["type"] for f in frames]
            assert types[0] == "accepted"
            assert (
                types.index("user_message") < types.index("tool_call") < types.index("tool_result")
            )
            assert types.index("tool_result") < types.index("final") < types.index("quota")
            final = next(f for f in frames if f["type"] == "final")
            assert final["text"] == "Heute ist Dienstag."
            seqs = [f["seq"] for f in frames if "seq" in f]
            assert seqs == sorted(seqs)

    def test_user_message_echo_carries_the_client_msg_id(self, env: Env) -> None:
        # The plugin draws the user's bubble on send and drops the echo whose
        # client_msg_id matches; without it, every message shows up twice.
        env.model.script.append(AIMessage(content="ok"))
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json(
                {"type": "send", "text": "Hi", "token": env.token(), "client_msg_id": "m1"}
            )
            frames = until(ws, "final")
            echo = next(f for f in frames if f["type"] == "user_message")
            assert echo["client_msg_id"] == "m1"

    def test_question_and_answer_round_trip(self, env: Env) -> None:
        env.model.script.extend(
            [
                call("ask_user", {"question": "Welcher Kurs?", "options": ["LF7"]}),
                AIMessage(content="Gut, LF7."),
            ]
        )
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "send", "text": "Prüfung?", "token": env.token()})
            frames = until(ws, "question")
            conv = frames[0]["conversation_id"]
            question = frames[-1]
            ws.send_json(
                {
                    "type": "answer",
                    "conversation_id": conv,
                    "interrupt_id": question["interrupt_id"],
                    "response": {"text": "LF7", "selected": ["LF7"]},
                    "token": env.token(),
                }
            )
            final = until(ws, "final")[-1]
            assert final["text"] == "Gut, LF7."

    def test_resume_after_reconnect_replays_missed_events(self, env: Env) -> None:
        env.model.script.extend([AIMessage(content="Antwort")])
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "send", "text": "Frage", "token": env.token()})
            frames = until(ws, "user_message")
            conv = frames[0]["conversation_id"]
            last_seq = frames[-1]["seq"]
        # The first socket is gone; the turn finished without it.
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "resume", "conversation_id": conv, "last_seq": last_seq})
            replayed = until(ws, "quota")
            assert replayed[0]["seq"] == last_seq + 1
            assert any(f["type"] == "final" and f["text"] == "Antwort" for f in replayed)

    def test_resume_of_foreign_conversation_is_not_found(self, env: Env) -> None:
        env.model.script.extend([AIMessage(content="privat")])
        with connect(env) as ws:
            authed(env, ws, user=7)
            ws.send_json({"type": "send", "text": "x", "token": env.token(7)})
            conv = until(ws, "quota")[0]["conversation_id"]
        with connect(env) as ws:
            authed(env, ws, user=8)
            ws.send_json({"type": "resume", "conversation_id": conv, "last_seq": 0})
            assert until(ws, "error")[-1]["code"] == "not_found"

    def test_unknown_frame_type(self, env: Env) -> None:
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "teleport"})
            assert until(ws, "error")[-1]["code"] == "unknown_type"

    def test_ping(self, env: Env) -> None:
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_rate_limited_user(self, env: Env) -> None:
        with connect(env) as ws:
            ws.send_json({"type": "auth", "token": env.token(quota={"day": 1})})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "send", "text": "x", "token": env.token(quota={"day": 1})})
            frame = until(ws, "rate_limited")[-1]
            assert frame["scope"] == "user" and frame["limit"] == 1


class TestConversationsApi:
    def _bearer(self, env: Env, user: int = 7) -> dict[str, str]:
        return {"Authorization": f"Bearer {env.token(user)}"}

    def _make(self, env: Env) -> str:
        env.model.script.extend([AIMessage(content="Antwort")])
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "send", "text": "Meine Frage", "token": env.token()})
            return str(until(ws, "quota")[0]["conversation_id"])

    def test_list_get_rename_archive(self, env: Env) -> None:
        conv = self._make(env)
        listed = env.client.get("/api/conversations", headers=self._bearer(env)).json()
        assert [c["id"] for c in listed] == [conv]
        assert listed[0]["title"] == "Meine Frage"
        detail = env.client.get(f"/api/conversations/{conv}", headers=self._bearer(env)).json()
        assert any(e["type"] == "final" for e in detail["events"])
        r = env.client.patch(
            f"/api/conversations/{conv}", json={"title": "Neu"}, headers=self._bearer(env)
        )
        assert r.status_code == 200
        assert (
            env.client.get("/api/conversations", headers=self._bearer(env)).json()[0]["title"]
            == "Neu"
        )
        assert (
            env.client.delete(f"/api/conversations/{conv}", headers=self._bearer(env)).status_code
            == 204
        )
        assert env.client.get("/api/conversations", headers=self._bearer(env)).json() == []

    def test_detail_carries_last_seq_and_tool_metadata(self, env: Env) -> None:
        conv = self._make(env)
        detail = env.client.get(f"/api/conversations/{conv}", headers=self._bearer(env)).json()
        assert detail["last_seq"] == max(e["seq"] for e in detail["events"])
        names = {t["name"] for t in detail["tools"]}
        assert "search_text" in names
        assert all({"name", "label", "icon"} <= set(t) for t in detail["tools"])

    def test_history_resumes_inside_a_streamed_run(self, env: Env) -> None:
        conv = self._make(env)
        url = f"/api/conversations/{conv}"
        last = env.client.get(url, headers=self._bearer(env)).json()["last_seq"]
        # A run of three deltas that went out live as last+1 .. last+3.
        for _ in range(3):
            env.on_loop(env.store.next_seq, conv)
        run = {"type": "token", "turn_id": "t", "delta": "Noch was.", "parts": [4, 4, 1]}
        env.on_loop(lambda: env.store.append_event(S, 7, conv, "t", run, seq=last + 3))
        full = env.client.get(f"{url}?after_seq={last}", headers=self._bearer(env)).json()
        assert [e["delta"] for e in full["events"]] == ["Noch was."]
        assert "parts" not in full["events"][0]
        rest = env.client.get(f"{url}?after_seq={last + 1}", headers=self._bearer(env)).json()
        assert [e["delta"] for e in rest["events"]] == [" was."]
        assert rest["last_seq"] == full["last_seq"] == last + 3

    def test_empty_conversation_has_last_seq_zero(self, env: Env) -> None:
        conv = self._make(env)
        last = env.client.get(f"/api/conversations/{conv}", headers=self._bearer(env)).json()
        url = f"/api/conversations/{conv}?after_seq={last['last_seq']}"
        assert env.client.get(url, headers=self._bearer(env)).json()["events"] == []
        assert env.client.get(url, headers=self._bearer(env)).json()["last_seq"] == last["last_seq"]

    def test_other_user_gets_404(self, env: Env) -> None:
        conv = self._make(env)
        r = env.client.get(f"/api/conversations/{conv}", headers=self._bearer(env, user=8))
        assert r.status_code == 404

    def test_requires_token(self, env: Env) -> None:
        assert env.client.get("/api/conversations").status_code == 401

    def test_cors_only_for_registered_sites(self, env: Env) -> None:
        ok = env.client.options(
            "/api/conversations",
            headers={"Origin": URL, "Access-Control-Request-Method": "GET"},
        )
        assert ok.headers.get("access-control-allow-origin") == URL
        bad = env.client.options(
            "/api/conversations",
            headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in bad.headers


class TestPolicyAndUsage:
    def test_policy_push_and_usage_report(self, env: Env) -> None:
        site_auth = {"Authorization": f"Bearer {env.secret}"}
        r = env.client.put("/ingest/policy", json={"user_tokens_per_day": 1}, headers=site_auth)
        assert r.status_code == 200
        with connect(env) as ws:
            authed(env, ws)
            ws.send_json({"type": "send", "text": "x", "token": env.token()})
            assert until(ws, "rate_limited")[-1]["scope"] == "user"
        usage = env.client.get("/ingest/usage?period=day&by=site", headers=site_auth)
        assert usage.status_code == 200
        assert usage.json() == {"period": "day", "by": "site", "rows": []}

    def test_policy_requires_site_auth(self, env: Env) -> None:
        assert env.client.put("/ingest/policy", json={}).status_code == 401
