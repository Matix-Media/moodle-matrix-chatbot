"""Third-party request logging must not drown out our own structured logs.

Assertions check the logger's own ``.level`` (what our code actually sets), not
``getEffectiveLevel()`` — pytest's log-capture plugin adjusts the root logger's
effective level around each test run, which would make effective-level assertions
flaky for reasons unrelated to the code under test.
"""

from __future__ import annotations

import logging

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
