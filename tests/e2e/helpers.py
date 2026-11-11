"""Helpers for the end-to-end tier (no langchain here: the runner image lacks it)."""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, Page, expect

MOODLE = os.environ.get("E2E_MOODLE_URL", "http://moodle")
API = os.environ.get("E2E_API_URL", "http://api:8000")
MOCK = os.environ.get("E2E_MOCK_URL", "http://mock-llm:9000")
SECRET_FILE = os.environ.get("E2E_SECRET_FILE", "/shared/site-secret")
PASSWORD = "Test1234!"
ADMIN_PASSWORD = "Admin1234!"


class Mock:
    """Drives the scripted model."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=MOCK, timeout=10)

    def script(self, *steps: dict[str, Any]) -> None:
        self.http.post("/__reset").raise_for_status()
        self.http.post("/__script", json=list(steps)).raise_for_status()

    def calls(self) -> list[list[dict[str, Any]]]:
        result: list[list[dict[str, Any]]] = self.http.get("/__calls").json()
        return result

    def everything_seen(self) -> str:
        return "\n".join(str(m.get("content")) for messages in self.calls() for m in messages)


def login(page: Page, username: str, password: str = PASSWORD) -> None:
    """Log in and wait for Moodle's whole post-login redirect chain to settle."""
    page.goto(f"{MOODLE}/login/index.php", wait_until="load")
    page.fill("#username", username)
    page.fill("#password", password)
    with page.expect_navigation(wait_until="load", timeout=30_000):
        page.click("#loginbtn")
    if page.locator("body#page-login-index").count():
        message = page.locator("#loginerrormessage, .loginerrors").first.text_content() or ""
        raise AssertionError(f"login as {username!r} failed: {message.strip()!r}")


def wait_for(predicate: Any, *, timeout: float, what: str, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last value: {last!r}")


def run_full_import(admin: BrowserContext, api: httpx.Client) -> dict[str, Any]:
    """Trigger the import from the plugin's status page and wait until it is indexed."""
    page = admin.new_page()
    page.goto(f"{MOODLE}/local/llmchat/admin/sync.php")
    page.get_by_role("button", name=re.compile("Run full import now")).click()
    page.close()

    def done() -> dict[str, Any] | None:
        status: dict[str, Any] = api.get("/ingest/status").json()
        pipeline = status.get("pipeline") or {}
        ready = (
            status.get("documents", 0) >= 3
            and status.get("pending_extraction", 1) == 0
            and pipeline.get("pending_vectors", 1) == 0
            and (status.get("last_sync") or {}).get("state") == "finished"
        )
        return status if ready else None

    result: dict[str, Any] = wait_for(
        done, timeout=240, what="the full import to be indexed", interval=3
    )
    return result


class Chat:
    """The chat page as a user sees it."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, query: str = "") -> Chat:
        """Open the chat and wait until its websocket is authenticated and ``ready``.

        The click handlers attach when the page's JavaScript initialises; sending
        earlier submits the form natively (a reload). The server's ``ready`` frame is
        the one reliable "usable now" signal.
        """
        ready = {"seen": False}

        def on_websocket(ws: Any) -> None:
            def on_frame(payload: Any) -> None:
                if isinstance(payload, str) and '"type":"ready"' in payload.replace(" ", ""):
                    ready["seen"] = True

            ws.on("framereceived", on_frame)

        self.page.on("websocket", on_websocket)
        self.page.goto(f"{MOODLE}/local/llmchat/chat.php{query}")
        expect(self.page.locator("[data-region=llmchat]")).to_be_visible()
        deadline = time.monotonic() + 30
        while not ready["seen"]:
            if time.monotonic() > deadline:
                raise AssertionError("the chat websocket never became ready")
            self.page.wait_for_timeout(200)  # pumps Playwright's event loop
        self.page.remove_listener("websocket", on_websocket)
        return self

    def ask(self, text: str) -> None:
        self.page.fill("[data-region=input]", text)
        self.page.click("[data-action=send]")

    def last_answer(self) -> Any:
        return self.page.locator(".local-llmchat-msg-assistant [data-region=text]").last

    def wait_answer(self, contains: str, timeout: float = 30_000) -> Any:
        answer = self.last_answer()
        expect(answer).to_contain_text(contains, timeout=timeout)
        return answer
