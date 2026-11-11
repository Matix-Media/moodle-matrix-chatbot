"""End-to-end tier: a real Moodle with local_llmchat, the real API, a real browser.

Runs inside the ``runner`` service of ``tests/e2e/docker-compose.yml`` so the browser,
Moodle and the API share one network and the same URLs. Only the model's decisions
are scripted (``mock_llm``); everything else — plugin sync, encryption, the agent,
access checks, the websocket and the chat UI — is the production path.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext

from e2e.helpers import (
    ADMIN_PASSWORD,
    API,
    SECRET_FILE,
    Chat,
    Mock,
    login,
    run_full_import,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def secret() -> str:
    with open(SECRET_FILE) as fh:
        return fh.read().strip()


@pytest.fixture(scope="session")
def api(secret: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {secret}"}, timeout=10)


@pytest.fixture
def mock() -> Mock:
    return Mock()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}, "locale": "de-DE"}


@pytest.fixture(scope="session")
def admin_context(browser: Browser) -> Iterator[BrowserContext]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    login(page, "admin", ADMIN_PASSWORD)
    page.close()
    yield context
    context.close()


@pytest.fixture(scope="session")
def imported(admin_context: BrowserContext, api: httpx.Client) -> dict[str, Any]:
    status: dict[str, Any] = api.get("/ingest/status").json()
    if (status.get("last_sync") or {}).get("state") == "finished" and status.get(
        "documents", 0
    ) >= 3:
        return status
    return run_full_import(admin_context, api)


@pytest.fixture
def student(browser: Browser, imported: dict[str, Any]) -> Iterator[Chat]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    login(page, "student")
    yield Chat(page)
    context.close()


@pytest.fixture
def teacher(browser: Browser, imported: dict[str, Any]) -> Iterator[Chat]:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    login(page, "teacher")
    yield Chat(page)
    context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """On a failing browser test, attach what the scripted model was shown.

    Most flakes are "the agent did not do what the script expected"; the model's
    side of the conversation shows why without re-running.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    try:
        calls = Mock().calls()
    except Exception as exc:  # the mock itself may be what is broken
        report.sections.append(("mock model conversation", f"unavailable: {exc!r}"))
        return
    lines: list[str] = []
    for index, messages in enumerate(calls):
        lines.append(f"--- model call {index}")
        for message in messages:
            if message.get("role") == "system":
                continue
            names = [t["function"]["name"] for t in message.get("tool_calls") or []]
            content = str(message.get("content"))[:300].replace("\n", " ⏎ ")
            lines.append(f"  {message['role']}: {content!r} {names or ''}")
    report.sections.append(("mock model conversation", "\n".join(lines) or "(no model calls)"))
