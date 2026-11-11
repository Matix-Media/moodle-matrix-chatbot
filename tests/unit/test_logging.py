"""Third-party request logging must not drown out our own structured logs.

Assertions check the logger's own ``.level`` (what our code actually sets), not
``getEffectiveLevel()`` — pytest's log-capture plugin adjusts the root logger's
effective level around each test run, which would make effective-level assertions
flaky for reasons unrelated to the code under test.
"""

from __future__ import annotations

import logging

import pytest

from moodle_llm_chat.logging import configure_logging


def test_own_namespace_is_left_alone() -> None:
    """We must not clamp our own logger namespace while quieting httpx."""
    configure_logging("DEBUG")
    assert logging.getLogger("moodle_llm_chat").level == logging.NOTSET


def test_httpx_request_lines_are_quieted() -> None:
    """Regression: httpx logs a bare 'HTTP Request: POST ... 200 OK' per call,
    which says nothing about what the request actually did. We log the meaningful
    event ourselves; httpx's own line is just noise.
    """
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_httpcore_is_quieted_too() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_payload_fields_are_redacted() -> None:
    """Course text and chats are encrypted at rest; logs must not leak them."""
    from moodle_llm_chat.logging import redact_payloads

    event = {"event": "x", "text": "secret", "content": "secret", "count": 3, "doc_id": "d1"}
    out = redact_payloads(None, "info", dict(event))
    assert out["text"] == "[redacted]"
    assert out["content"] == "[redacted]"
    assert out["count"] == 3 and out["doc_id"] == "d1"


def test_json_logs_include_tracebacks(capsys: pytest.CaptureFixture[str]) -> None:
    """Production logs are JSON; an exception must arrive with its traceback."""
    import json

    import structlog

    configure_logging("INFO", pretty=False)
    try:
        raise ValueError("kaputt")
    except ValueError:
        structlog.get_logger("t").exception("job_error", doc_id="d1")
    line = [ln for ln in capsys.readouterr().err.splitlines() if "job_error" in ln][-1]
    event = json.loads(line)
    assert "ValueError: kaputt" in event["exception"]
