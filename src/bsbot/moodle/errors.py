"""Moodle error taxonomy.

The distinctions here are deliberate: each type maps to a different *action* the
operator must take. Auth errors mean fix the credentials, ``MoodleWebServicesDisabled``
means an admin must change a Moodle setting, transport errors mean try again later,
and ``MoodleFunctionUnavailable`` means this site simply cannot serve that data and
the crawler should skip it.
"""

from __future__ import annotations


class MoodleError(Exception):
    """Base class for every Moodle failure."""


class MoodleAuthError(MoodleError):
    """Credentials or token rejected."""

    def __init__(self, message: str, errorcode: str | None = None) -> None:
        super().__init__(message)
        self.errorcode = errorcode


class MoodleWebServicesDisabled(MoodleAuthError):
    """Web services are switched off site-wide — only an admin can fix this."""


class MoodleAPIError(MoodleError):
    """Moodle returned an application-level exception."""

    def __init__(self, message: str, errorcode: str | None = None, exception: str | None = None):
        super().__init__(message)
        self.errorcode = errorcode
        self.exception = exception


class MoodleFunctionUnavailable(MoodleError):
    """This site does not expose the requested web service function."""


class MoodleTransportError(MoodleError):
    """Network or HTTP failure that survived the retry budget."""
