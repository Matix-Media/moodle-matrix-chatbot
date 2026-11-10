"""Parse the ``since`` / ``until`` arguments tools accept.

Accepted: ISO dates (``2026-11-01``), ISO datetimes, relative offsets (``-7d``,
``-2w``, ``-3m`` ≈ 30-day months, ``+1d``), and ``today``/``heute``,
``yesterday``/``gestern``, ``tomorrow``/``morgen``. Bare dates mean the start of the
day for ``since`` and the end of the day for ``until``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

_RELATIVE_RE = re.compile(r"^([+-])\s*(\d+)\s*([dwmh])$", re.I)
_WORDS = {
    "today": 0,
    "heute": 0,
    "yesterday": -1,
    "gestern": -1,
    "tomorrow": 1,
    "morgen": 1,
}
_UNITS = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
}


def parse_moment(value: str | None, *, now: datetime, end_of_day: bool = False) -> int | None:
    """Epoch seconds for ``value`` relative to ``now`` (tz-aware), or ``None``."""
    if value is None or not value.strip():
        return None
    text = value.strip().lower()
    tz = now.tzinfo

    def day_bound(d: date) -> int:
        bound = time.max if end_of_day else time.min
        return int(datetime.combine(d, bound, tzinfo=tz).timestamp())

    if text in _WORDS:
        return day_bound(now.date() + timedelta(days=_WORDS[text]))
    match = _RELATIVE_RE.match(text)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        delta = _UNITS[match.group(3).lower()] * int(match.group(2))
        moment = now + sign * delta
        if match.group(3).lower() == "h":
            return int(moment.timestamp())
        return day_bound(moment.date())
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f"unrecognised date {value!r}; use YYYY-MM-DD, -7d, -2w or 'today'"
        ) from None
    if len(value.strip()) <= 10:
        return day_bound(parsed.date())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return int(parsed.timestamp())


def format_date(epoch: int | None, *, now: datetime) -> str:
    if not epoch:
        return "unbekannt"
    return datetime.fromtimestamp(epoch, now.tzinfo).strftime("%d.%m.%Y")
