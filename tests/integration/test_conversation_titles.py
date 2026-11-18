"""The utility model names a new conversation; the list shows it when the answer is done."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from support import ScriptedModel
from support.harness import MOODLE, Api, ws_until

USER = 7
SCOPE = {"cmids": {10}, "courses": {2}}


def socket(api: Api) -> Any:
    return api.http.websocket_connect("wss://testserver/ws/chat", headers={"origin": MOODLE})


def test_a_new_conversation_is_named_by_the_utility_model(api: Api) -> None:
    api.model.script.append(AIMessage(content="Im Raum B204."))
    api.title_model = ScriptedModel(script=[AIMessage(content='"Unterrichtsraum."')])
    with api.running(), socket(api) as ws:
        ws.send_json({"type": "auth", "token": api.token(USER, **SCOPE)})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "send",
                "text": "Wo ist morgen der Unterricht?",
                "token": api.token(USER, **SCOPE),
            }
        )
        frames = ws_until(ws, "final")
        title = next(f for f in frames if f["type"] == "title")
        assert title["title"] == "Unterrichtsraum"
        # What the plugin asks for when ``final`` arrives:
        bearer = {"Authorization": f"Bearer {api.token(USER, **SCOPE)}"}
        listed = api.http.get("/api/conversations", headers=bearer).json()
        assert [c["title"] for c in listed] == ["Unterrichtsraum"]
