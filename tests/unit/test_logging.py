"""Regression: nio's internal per-event logging was drowning out real signal.

``logging.basicConfig`` sets the root logger level, which nio's own
``logging.getLogger(__name__)`` calls inherit — every "Room X handling event of
type Y" trace line was showing up at INFO alongside bsbot's own structured logs.

Assertions check the logger's own ``.level`` (what our code actually sets), not
``getEffectiveLevel()`` — pytest's log-capture plugin adjusts the root logger's
effective level around each test run, which would make effective-level assertions
flaky for reasons unrelated to the code under test.
"""

from __future__ import annotations

import logging

from bsbot.shared.logging import configure_logging


def test_nio_internal_logging_is_quieted() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("nio").level >= logging.WARNING


def test_bsbot_namespace_is_left_alone() -> None:
    """We must not clamp our own logger namespace while quieting nio's."""
    configure_logging("DEBUG")
    assert logging.getLogger("bsbot").level == logging.NOTSET


def test_httpx_request_lines_are_quieted() -> None:
    """Regression: httpx logs a bare 'HTTP Request: POST ... 200 OK' per call,
    which says nothing about what the request actually did. bsbot logs the real
    content itself (see rag.pipeline, llm.embed); httpx's own line is just noise.
    """
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_httpcore_is_quieted_too() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("httpcore").level >= logging.WARNING
