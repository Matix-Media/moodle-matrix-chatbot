"""Structured logging setup.

Third-party libraries (nio in particular) log through plain stdlib
``logging.getLogger(__name__).warning(...)`` calls, not through structlog. Without
bridging the two, those lines render as bare, unformatted text while bsbot's own
log lines carry a timestamp and level tag — the two interleave but look like they
came from different programs. ``structlog.stdlib.ProcessorFormatter`` routes both
through the same renderer, so every line in the log looks the same regardless of
which library emitted it.
"""

from __future__ import annotations

import logging

import structlog

#: nio logs a trace line for nearly every sync event ("Room X handling event of
#: type Y", "Adding new device to the device store...") at INFO/DEBUG. httpx logs a
#: bare "HTTP Request: POST ... 200 OK" per call that says nothing about what the
#: request actually did — bsbot logs the real content itself instead (see
#: rag.pipeline, llm.embed). Left alone both drown out bsbot's own structured logs,
#: which is what actually matters when something is wrong.
_QUIET_LOGGERS = ("nio", "httpx", "httpcore")


def configure_logging(level: str = "INFO", *, pretty: bool = True) -> None:
    """Configure structlog once, at process start."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    ]
    renderer = structlog.dev.ConsoleRenderer() if pretty else structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        # Routes structlog's own calls through stdlib logging too, so both structlog
        # and plain-stdlib callers (nio) end up on the same handler and formatter.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied only to records that did NOT come from structlog (e.g. nio's
        # bare logging.warning() calls) before they reach the shared renderer.
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
