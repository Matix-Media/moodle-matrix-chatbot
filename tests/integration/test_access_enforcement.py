"""A student must never see what Moodle would not show them — through any tool."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from starlette.websockets import WebSocketDisconnect

from support import call, last_tool_output
from support.harness import MOODLE, Api, document, ws_until

STUDENT = 7
TEACHER = 9


def populate(api: Api) -> None:
    api.push_courses(
        {"course_id": 2, "fullname": "Netzwerke LF7", "shortname": "LF7"},
        {"course_id": 3, "fullname": "Prüfungsvorbereitung", "shortname": "PV"},
    )
    api.push_documents(
        document("2:10:a", text="VLANs trennen Broadcast-Domänen.", module="VLAN"),
        document("2:11:solution", text="Musterlösung Zebrafink: Antwort C.", module="Lösung"),
        document("3:20:b", text="Prüfungsfragen Zebrafink aus dem Vorjahr.", module="Altklausur"),
    )
    api.idle()
    # Moodle shows the student course 2 except the hidden solution; the teacher sees all.
    api.moodle.visible[STUDENT] = {10}
    api.moodle.visible[TEACHER] = {10, 11, 20}


def ask(
    api: Api, user: int, cmids: set[int], courses: set[int], question: str = "?"
) -> list[dict[str, Any]]:
    with api.http.websocket_connect("wss://testserver/ws/chat", headers={"origin": MOODLE}) as ws:
        ws.send_json({"type": "auth", "token": api.token(user, cmids=cmids, courses=courses)})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "send",
                "text": question,
                "token": api.token(user, cmids=cmids, courses=courses),
            }
        )
        return ws_until(ws, "quota")


def everything_the_model_saw(api: Api) -> str:
    # Guard against vacuous passes: every scripted step (so every tool call) really ran.
    assert api.model.script == [], "the agent stopped before running all tool calls"
    return "\n".join(str(m.content) for msgs in api.model.seen for m in msgs)


def probe(*tool_calls: tuple[str, dict[str, Any]]) -> list[Any]:
    steps: list[Any] = [call(name, args, f"c{i}") for i, (name, args) in enumerate(tool_calls)]
    steps.append(AIMessage(content="Fertig."))
    return steps


class TestScope:
    @pytest.mark.parametrize(
        "tool_calls",
        [
            [("search_content", {"query": "Zebrafink"})],
            [("search_text", {"query": "Zebrafink"})],
            [("search_regex", {"pattern": "Zebra\\w+"})],
            [("list_dir", {"path": "/"}), ("list_dir", {"path": "/LF7/Woche 1"})],
            [("read_file", {"path": "/PV/Woche 1/Altklausur"})],
            [("read_file", {"path": "/LF7/Woche 1/Lösung"})],
            [("list_documents", {"order": "newest"})],
        ],
        ids=["semantic", "text", "regex", "list_dir", "read_other_course", "read_hidden", "newest"],
    )
    def test_no_tool_reveals_out_of_scope_content(
        self, api: Api, tool_calls: list[tuple[str, dict[str, Any]]]
    ) -> None:
        api.model.script.extend(probe(*tool_calls))
        with api.running():
            populate(api)
            # The token is honest (from the plugin): course 2 only, the solution excluded.
            ask(api, STUDENT, {10}, {2})
        seen = everything_the_model_saw(api)
        assert "Zebrafink" not in seen
        assert "Musterlösung" not in seen
        assert "Prüfungsvorbereitung" not in seen

    def test_teacher_sees_both_courses(self, api: Api) -> None:
        api.model.script.extend(probe(("search_text", {"query": "Zebrafink"})))
        with api.running():
            populate(api)
            ask(api, TEACHER, {10, 11, 20}, {2, 3})
        seen = everything_the_model_saw(api)
        assert "Musterlösung Zebrafink" in seen and "Prüfungsfragen Zebrafink" in seen

    def test_probes_are_not_vacuous(self, api: Api) -> None:
        """Positive control: the same probe does find the visible VLAN document."""
        api.model.script.extend(probe(("search_regex", {"pattern": "Broadcast\\S+"})))
        with api.running():
            populate(api)
            ask(api, STUDENT, {10}, {2})
        assert "Broadcast-Domänen" in everything_the_model_saw(api)


class TestLiveCheck:
    def test_moodle_hiding_a_module_overrides_a_stale_token(self, api: Api) -> None:
        """The token still lists cmid 11, but Moodle hid it a moment ago."""
        api.model.script.extend(probe(("read_file", {"path": "/LF7/Woche 1/Lösung"})))
        with api.running():
            populate(api)
            ask(api, STUDENT, {10, 11}, {2})
        assert "Musterlösung" not in everything_the_model_saw(api)
        assert "keinen Zugriff" in everything_the_model_saw(api)

    def test_moodle_down_fails_closed_and_the_agent_is_told(self, api: Api) -> None:
        def check(messages: list[BaseMessage]) -> AIMessage:
            assert "nicht erreichbar" in last_tool_output(messages)
            return AIMessage(content="Moodle ist gerade nicht erreichbar.")

        api.model.script.extend([call("search_text", {"query": "VLAN"}), check])
        with api.running():
            populate(api)
            api.moodle.down = True
            frames = ask(api, STUDENT, {10}, {2})
        assert "Broadcast" not in everything_the_model_saw(api)
        result = next(f for f in frames if f["type"] == "tool_result")
        assert result["error"] == "moodle_unavailable"

    def test_wrong_callback_secret_is_refused_by_moodle(self, api: Api) -> None:
        api.model.script.extend(probe(("search_text", {"query": "VLAN"})))
        with api.running():
            populate(api)
            api.moodle.secret = "rotated-elsewhere"
            ask(api, STUDENT, {10}, {2})
        assert "Broadcast" not in everything_the_model_saw(api)


class TestIdentity:
    def test_users_cannot_read_each_others_history(self, api: Api) -> None:
        api.model.script.extend([AIMessage(content="Privat für 7.")])
        with api.running():
            populate(api)
            conv = ask(api, STUDENT, {10}, {2}, "Meine private Frage")[0]["conversation_id"]
            other = {"Authorization": f"Bearer {api.token(TEACHER, cmids={10}, courses={2})}"}
            assert api.http.get(f"/api/conversations/{conv}", headers=other).status_code == 404
            assert api.http.get("/api/conversations", headers=other).json() == []
            with api.http.websocket_connect(
                "wss://testserver/ws/chat", headers={"origin": MOODLE}
            ) as ws:
                ws.send_json({"type": "auth", "token": api.token(TEACHER, cmids={10}, courses={2})})
                ws.receive_json()
                ws.send_json({"type": "resume", "conversation_id": conv, "last_seq": 0})
                assert ws_until(ws, "error")[-1]["code"] == "not_found"

    def test_token_from_another_site_secret_is_rejected(self, api: Api) -> None:
        with api.running():
            populate(api)
            api.secret, real = "mlc_forged", api.secret
            forged = api.token(STUDENT, cmids={10, 11, 20}, courses={2, 3})
            api.secret = real
            with api.http.websocket_connect(
                "wss://testserver/ws/chat", headers={"origin": MOODLE}
            ) as ws:
                ws.send_json({"type": "auth", "token": forged})
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 4401
