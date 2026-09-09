"""Shared-bucket rate limiting for the web chat API — see specs/014-web-chat.md AC-7."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime


class RateLimiter:
    """Burst-per-minute + daily quota, mirroring `BotPolicy`'s shape (spec
    009) — but a single bucket: every caller presents the same shared token,
    so there is no per-visitor identity to key limits on.
    """

    def __init__(
        self,
        *,
        max_per_minute: int = 20,
        max_per_day: int = 200,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_per_minute = max_per_minute
        self._max_per_day = max_per_day
        self._now = now or (lambda: datetime.now(UTC))
        self._recent: list[float] = []
        self._day: tuple[date, int] | None = None

    def allow(self) -> bool:
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < 60.0]
        if len(self._recent) >= self._max_per_minute:
            return False

        today = self._now().date()
        last_day, count = self._day or (today, 0)
        if last_day != today:
            count = 0
        if count >= self._max_per_day:
            self._day = (last_day, count)
            return False

        self._recent.append(now)
        self._day = (today, count + 1)
        return True
