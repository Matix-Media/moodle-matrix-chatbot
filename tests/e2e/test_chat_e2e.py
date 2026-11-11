"""Browser scenarios against the full stack (Moodle + local_llmchat + API + scripted model)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, expect

from e2e.helpers import MOODLE, Chat, Mock, login, run_full_import, wait_for


class TestImport:
    def test_test_connection_and_full_import(
        self, admin_context: BrowserContext, api: httpx.Client
    ) -> None:
        page = admin_context.new_page()
        page.goto(f"{MOODLE}/local/llmchat/admin/sync.php")
        with page.expect_navigation(wait_until="load", timeout=30_000):
            page.get_by_role("button", name=re.compile("Test connection")).click()
        expect(page.locator(".alert-danger")).to_have_count(0)
        page.close()
        status = run_full_import(admin_context, api)
        assert status["courses"] >= 2
        assert status["skipped"] == 0


class TestStudentChat:
    def test_cited_answer_with_tool_cards(self, student: Chat, mock: Mock) -> None:
        mock.script(
            {"tool": "search_text", "args": {"query": "Raum"}},
            {"text": "Der Unterricht ist montags in Raum B204 [1].", "expect": "B204"},
        )
        student.open().ask("Wo ist der Unterricht?")
        answer = student.wait_answer("Raum B204")
        tool = student.page.locator("[data-region=tool]").first
        expect(tool).to_be_visible()
        expect(tool.locator("[data-region=done]")).to_be_visible()
        cite = answer.locator("a.local-llmchat-cite")
        expect(cite).to_have_count(1)
        assert "/mod/page/view.php?id=" in (cite.get_attribute("href") or "")

    def test_clarifying_question_round_trip(self, student: Chat, mock: Mock) -> None:
        mock.script(
            {
                "tool": "ask_user",
                "args": {"question": "Welcher Kurs?", "options": ["LF7", "Anderer"]},
            },
            {"text": "Dann schaue ich in LF7 nach.", "expect": "LF7"},
        )
        student.open().ask("Wann ist die Prüfung?")
        option = student.page.locator(
            "[data-region=interaction] [data-action=option]", has_text="LF7"
        )
        expect(option).to_be_visible(timeout=30_000)
        option.click()
        student.wait_answer("Dann schaue ich in LF7 nach.")

    def test_history_survives_a_reload(self, student: Chat, mock: Mock) -> None:
        mock.script({"text": "Gespeicherte Antwort zum Merken."})
        student.open().ask("Bitte merken")
        student.wait_answer("Gespeicherte Antwort")
        student.page.reload()
        conversation = student.page.locator(
            "[data-region=conversations] [data-action=open-conversation]", has_text="Bitte merken"
        )
        expect(conversation.first).to_be_visible(timeout=15_000)
        conversation.first.click()
        student.wait_answer("Gespeicherte Antwort")

    def test_connection_drop_mid_answer_loses_and_duplicates_nothing(
        self, student: Chat, mock: Mock
    ) -> None:
        text = "Eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf."
        mock.script({"text": text, "delay": 4})
        student.open().ask("Zähl bitte")
        expect(student.last_answer()).to_contain_text("Eins", timeout=15_000)
        student.page.context.set_offline(True)
        student.page.wait_for_timeout(2_500)
        student.page.context.set_offline(False)
        answer = student.wait_answer("zwölf.", timeout=45_000)
        assert answer.inner_text().count("Eins") == 1


class TestRights:
    def test_student_never_sees_other_courses_or_hidden_content(
        self, student: Chat, mock: Mock
    ) -> None:
        mock.script(
            {"tool": "search_text", "args": {"query": "Zebrafink Kranich"}},
            {"tool": "list_dir", "args": {"path": "/"}},
            {"text": "Dazu habe ich nichts gefunden."},
        )
        student.open().ask("Was steht in der Altklausur?")
        student.wait_answer("nichts gefunden")
        tool_outputs = "\n".join(
            str(m.get("content")) for msgs in mock.calls() for m in msgs if m.get("role") == "tool"
        )
        # Positive control: the tools did run and did show the student's own course …
        assert "[Ordner] LF7/" in tool_outputs
        # … and nothing from the other course or the hidden module, through any tool.
        for forbidden in ("Zebrafink", "Kranich", "Prüfungsvorbereitung", "PV/", "Musterlösung"):
            assert forbidden not in tool_outputs, forbidden

    def test_teacher_sees_both_courses(self, teacher: Chat, mock: Mock) -> None:
        mock.script(
            {"tool": "search_text", "args": {"query": "Zebrafink"}},
            {"text": "Gefunden in der Altklausur [1].", "expect": "Zebrafink"},
        )
        teacher.open().ask("Was steht in der Altklausur?")
        teacher.wait_answer("Gefunden in der Altklausur")

    def test_other_student_cannot_open_my_conversation(
        self, student: Chat, mock: Mock, browser: Any
    ) -> None:
        mock.script({"text": "Nur für Sophie."})
        student.open().ask("Private Frage von Sophie")
        student.wait_answer("Nur für Sophie")
        context = browser.new_context()
        page = context.new_page()
        login(page, "student2")
        Chat(page).open()
        expect(
            page.locator("[data-region=conversations]", has_text="Private Frage von Sophie")
        ).to_have_count(0)
        context.close()


class TestApiStatus:
    def test_usage_is_metered_and_reported_to_the_plugin(
        self, student: Chat, mock: Mock, api: httpx.Client
    ) -> None:
        mock.script({"text": "Kurze Antwort."})
        student.open().ask("Hallo")
        student.wait_answer("Kurze Antwort.")
        rows = wait_for(
            lambda: api.get("/ingest/usage", params={"period": "day", "by": "user"}).json()["rows"],
            timeout=15,
            what="usage rows",
        )
        assert all(r["tokens"] > 0 for r in rows)


class TestLayout:
    def test_full_page_is_full_height_from_the_first_paint(self, student: Chat) -> None:
        """Course and global chat: one width, as tall as the window, and no growth on load.

        The height is measured twice: at DOMContentLoaded (the stylesheet applies, the chat's
        JavaScript has not run yet) and after the page has loaded. Equal heights mean the
        stylesheet alone sizes the card, so it cannot jump once the page has loaded.
        """
        page = student.page
        page.add_init_script(
            """document.addEventListener('DOMContentLoaded', () => {
                const panel = document.querySelector('[data-region=panel]');
                window.__panelAtDomReady = panel ? panel.getBoundingClientRect().height : -1;
            });"""
        )
        widths = []
        for query in ("", f"?courseid={course_id(page, 'LF7')}"):
            page.goto(f"{MOODLE}/local/llmchat/chat.php{query}", wait_until="load")
            page.wait_for_timeout(500)
            panel = page.locator("[data-region=panel]")
            box = panel.bounding_box()
            assert box is not None
            assert 900 - 120 < box["y"] + box["height"] <= 900, (query, box)
            assert page.evaluate("window.__panelAtDomReady") == box["height"], query
            assert panel.evaluate("el => el.style.height") == "", "sized by the stylesheet"
            # The same page width (an open course index may still take some of it).
            widths.append(
                page.evaluate("getComputedStyle(document.getElementById('topofscroll')).maxWidth")
            )
        assert widths == ["1120px", "1120px"], widths


class TestBlock:
    def test_block_chat_continues_in_the_full_page_and_back(
        self, student: Chat, mock: Mock
    ) -> None:
        mock.script({"text": "Antwort aus dem Block."})
        student.open_block("LF7").ask("Frage im Block")
        student.wait_answer("Antwort aus dem Block.")
        block = student.page.locator(".block_llmchat")
        expect(block.locator("[data-region=conversations]")).to_have_count(0)

        # The other conversations are in the full page, which opens this one.
        expand = block.locator("[data-region=expand]")
        expect(expand).to_have_attribute("href", re.compile(r"conversation="))
        with student.page.expect_navigation(wait_until="load"):
            expand.click()
        assert "/local/llmchat/chat.php" in student.page.url
        student.wait_answer("Antwort aus dem Block.")
        expect(
            student.page.locator("[data-region=conversations] .active", has_text="Frage im Block")
        ).to_be_visible()

        # Back in the course, the block still shows that conversation.
        student.open_block("LF7")
        student.wait_answer("Antwort aus dem Block.")


def course_id(page: Any, shortname: str) -> int:
    """Course id from the course page URL Moodle redirects a shortname to."""
    page.goto(f"{MOODLE}/course/view.php?name={shortname}")
    match = re.search(r"[?&]id=(\d+)", page.url)
    if match:
        return int(match.group(1))
    body = page.locator("body").get_attribute("class") or ""
    found = re.search(r"\bcourse-(\d+)\b", body)
    assert found, page.url
    return int(found.group(1))
