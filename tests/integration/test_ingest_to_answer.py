"""From the plugin's push to a cited, streamed answer — all real components."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

from support import call, last_tool_output, pdf_bytes
from support.harness import Api, document, file_document, read_disk, ws_until

SKRIPT = pdf_bytes(
    [
        "Subnetting: Ein /24-Netz hat 254 nutzbare Hostadressen. " * 3,
        "Die Abschlussprüfung im Lernfeld 7 findet am 24.11.2026 in Raum B204 statt. " * 2,
    ]
)


def populate(api: Api) -> None:
    api.push_courses({"course_id": 2, "fullname": "Netzwerke LF7", "shortname": "LF7"})
    result = api.push_documents(
        document("2:10:intro", text="Willkommen im Kurs Netzwerke.", module="Begrüßung"),
        document(
            "2:11:page",
            kind="html",
            text="<h1>Stundenplan</h1><p>Montag 8:00 Uhr Raum A101</p><script>x()</script>",
            module="Stundenplan",
        ),
        file_document(
            "2:12:pdf", SKRIPT, "skript.pdf", module="Skript", section="Woche 2", section_order=2
        ),
        document(
            "2:13:link",
            kind="external",
            external_url="https://hackmd.io/LF7notes",
            module="Mitschrift",
        ),
    )
    assert result["needs_blob"]  # the PDF bytes have not been uploaded yet
    api.upload(SKRIPT)
    api.idle()


class TestIngest:
    def test_every_kind_of_document_becomes_searchable(self, api: Api) -> None:
        api.web.get("https://hackmd.io/LF7notes.md").respond(
            200, text="# Mitschrift\nVLAN-Tagging nach 802.1Q."
        )
        with api.running():
            populate(api)
            status = api.http.get("/ingest/status", headers=api.site_headers).json()
            assert status["documents"] == 4
            assert status["pending_extraction"] == 0
            assert status["pipeline"]["pending_vectors"] == 0
            store = api.store
            for word, doc_id in (
                ("Willkommen", "2:10:intro"),
                ("A101", "2:11:page"),
                ("B204", "2:12:pdf"),
                ("802.1Q", "2:13:link"),
            ):
                ids = api.on_loop(lambda w=word: store.fts_chunk_ids("schule", f'"{w}"', limit=3))
                (chunk,) = api.on_loop(lambda i=ids: store.chunks("schule", i[:1]))
                assert chunk.doc_id == doc_id, word
            pages = [s.page for s in api.on_loop(lambda: store.segments("schule", "2:12:pdf"))]
            assert pages == [1, 2]
            html = api.on_loop(lambda: store.segments("schule", "2:11:page"))[0].text
            assert "x()" not in html

    def test_nothing_is_stored_in_plaintext(self, api: Api) -> None:
        api.web.get("https://hackmd.io/LF7notes.md").respond(200, text="Geheimnotiz Quokka")
        with api.running():
            populate(api)
        disk = read_disk(api.data_dir)
        for secret in (b"Abschlusspr", b"Quokka", b"Willkommen", b"Stundenplan", b"Netzwerke LF7"):
            assert secret not in disk, secret


class TestAnswer:
    def test_student_gets_a_cited_answer_over_the_websocket(self, api: Api) -> None:
        api.web.get("https://hackmd.io/LF7notes.md").respond(200, text="Notizen")
        api.moodle.visible[7] = {10, 11, 12, 13}

        def read_hit(messages: list[BaseMessage]) -> AIMessage:
            assert "/LF7/Woche 2/Skript" in last_tool_output(messages)
            return call("read_file", {"path": "/LF7/Woche 2/Skript", "page": 2}, "c2")

        def answer(messages: list[BaseMessage]) -> AIMessage:
            assert "Raum B204" in last_tool_output(messages)
            return AIMessage(content="Die Prüfung ist am 24.11.2026 in Raum B204 [1].")

        api.model.script.extend(
            [call("search_content", {"query": "Abschlussprüfung Raum"}), read_hit, answer]
        )
        with api.running():
            populate(api)
            with api.http.websocket_connect(
                "wss://testserver/ws/chat", headers={"origin": "https://moodle.schule.test"}
            ) as ws:
                ws.send_json(
                    {"type": "auth", "token": api.token(7, cmids={10, 11, 12, 13}, courses={2})}
                )
                assert ws.receive_json()["type"] == "ready"
                ws.send_json(
                    {
                        "type": "send",
                        "text": "Wann und wo ist die Prüfung?",
                        "course_id": 2,
                        "token": api.token(7, cmids={10, 11, 12, 13}, courses={2}),
                    }
                )
                frames = ws_until(ws, "quota")
        types = [f["type"] for f in frames]
        assert types.count("tool_call") == 2 and types.count("tool_result") == 2
        final = next(f for f in frames if f["type"] == "final")
        assert final["text"].endswith("[1].")
        (citation,) = final["citations"]
        assert [s["name"] for s in citation["location"]] == ["LF7", "Woche 2", "Skript"]
        assert citation["location"][-1] == {"kind": "module", "id": "12", "name": "Skript"}
        assert citation["url"] == "https://moodle.schule.test/mod/page/view.php?id=12"
        assert citation["course_name"] == "Netzwerke LF7"
        # Every document body shown to the model was confirmed live with Moodle first.
        assert any(c["action"] == "check_access" and 12 in c["cmids"] for c in api.moodle.calls)
        # The system prompt told the model who is asking and from which course.
        system = str(api.model.seen[0][0].content)
        assert "Nutzer 7" in system and "Aktueller Kurs: Netzwerke LF7" in system
