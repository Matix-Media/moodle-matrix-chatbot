"""Token quotas per user and per site.

Three layers; the effective limit is always the strictest:

1. **Site policy**, set by the Moodle admin in the plugin and pushed to the API:
   site-wide day/month budgets, default per-user budgets, a per-turn cap.
2. **The user's effective limits**, computed by the plugin from per-user overrides
   and the ``local/llmchat:unlimited`` capability, carried in the chat token
   (``QuotaClaim``). An exempt user skips user limits; site limits still apply unless
   the policy says exempt users bypass them.
3. **API ceilings**, set by the operator (``moodle-llm-chat site limits``) — a hard cap
   the plugin cannot raise, plus a daily ingest (embedding/OCR) budget.

Windows are calendar day and month in the site's timezone. A turn is refused up front
when less than ``min_headroom_tokens`` remain; a running turn that crosses a limit is
wrapped up with one final answer instead of being cut off.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from moodle_llm_chat.access.scope import QuotaClaim
from moodle_llm_chat.agent.events import UsageEvent
from moodle_llm_chat.config import QuotaSection
from moodle_llm_chat.index.store import Store

CHAT_PURPOSES = ("chat", "utility")
INGEST_PURPOSES = ("embed", "ocr")


class QuotaPolicy(BaseModel):
    site_tokens_per_day: int | None = None
    site_tokens_per_month: int | None = None
    user_tokens_per_day: int | None = None
    user_tokens_per_month: int | None = None
    max_tokens_per_turn: int | None = None
    count_reasoning: bool = True
    exempt_users_bypass_site: bool = False
    timezone: str | None = None


class SiteCeilings(BaseModel):
    tokens_per_day: int | None = None
    tokens_per_month: int | None = None
    ingest_tokens_per_day: int | None = None


class QuotaExceeded(Exception):
    def __init__(self, scope: str, used: int, limit: int, resets_at: int) -> None:
        super().__init__(f"{scope} token limit reached ({used}/{limit})")
        self.scope = scope
        self.used = used
        self.limit = limit
        self.resets_at = resets_at


class QuotaStatus(BaseModel):
    used_today: int
    limit_today: int | None
    used_month: int
    limit_month: int | None
    resets_at: int
    exempt: bool


def _min(*values: int | None) -> int | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


class QuotaService:
    def __init__(
        self,
        store: Store,
        settings: QuotaSection,
        *,
        timezone: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self._default_tz = timezone
        self._clock = clock or (lambda: datetime.now(ZoneInfo(timezone)))

    # -- configuration ------------------------------------------------------

    def policy(self, site_id: str) -> QuotaPolicy:
        return QuotaPolicy.model_validate(self.store.site_policy(site_id))

    def set_policy(self, site_id: str, policy: QuotaPolicy) -> None:
        self.store.set_site_policy(site_id, policy.model_dump())

    def ceilings(self, site_id: str) -> SiteCeilings:
        return SiteCeilings.model_validate(self.store.site_ceilings(site_id))

    def set_ceilings(self, site_id: str, ceilings: SiteCeilings) -> None:
        self.store.set_site_ceilings(site_id, ceilings.model_dump())

    # -- windows ------------------------------------------------------------

    def _windows(self, policy: QuotaPolicy) -> tuple[int, int, int, int]:
        tz = ZoneInfo(policy.timezone or self._default_tz)
        now = self._clock().astimezone(tz)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = (day + timedelta(days=1)).replace(tzinfo=tz)
        month = day.replace(day=1)
        next_month = (month + timedelta(days=32)).replace(day=1)
        return (
            int(day.timestamp()),
            int(next_day.timestamp()),
            int(month.timestamp()),
            int(next_month.timestamp()),
        )

    def _tokens(self, rows: list[Any], policy: QuotaPolicy) -> int:
        total = 0
        for r in rows:
            total += int(r["input_tokens"]) + int(r["output_tokens"])
            if not policy.count_reasoning:
                total -= int(r["reasoning_tokens"])
        return total

    def _used(
        self, site_id: str, policy: QuotaPolicy, *, since: int, until: int, user_id: int | None
    ) -> int:
        rows = self.store.usage_rows(
            site_id, since=since, until=until, purposes=CHAT_PURPOSES, user_id=user_id
        )
        return self._tokens(rows, policy)

    # -- limits -------------------------------------------------------------

    def _limits(
        self, site_id: str, claim: QuotaClaim | None
    ) -> tuple[QuotaPolicy, list[tuple[str, int, int | None, Literal["day", "month"]]]]:
        """(policy, [(scope, user_id_or_-1, limit, window)])."""
        policy = self.policy(site_id)
        ceilings = self.ceilings(site_id)
        exempt = bool(claim and claim.exempt)
        checks: list[tuple[str, int, int | None, Literal["day", "month"]]] = []
        if not exempt:
            user_day = claim.day if claim and claim.day is not None else policy.user_tokens_per_day
            user_month = (
                claim.month if claim and claim.month is not None else policy.user_tokens_per_month
            )
            checks += [("user", 0, user_day, "day"), ("user", 0, user_month, "month")]
        bypass = exempt and policy.exempt_users_bypass_site
        site_day = _min(None if bypass else policy.site_tokens_per_day, ceilings.tokens_per_day)
        site_month = _min(
            None if bypass else policy.site_tokens_per_month, ceilings.tokens_per_month
        )
        checks += [("site", -1, site_day, "day"), ("site", -1, site_month, "month")]
        return policy, checks

    def _evaluate(
        self, site_id: str, user_id: int, claim: QuotaClaim | None
    ) -> tuple[QuotaExceeded | None, QuotaPolicy]:
        policy, checks = self._limits(site_id, claim)
        day, next_day, month, next_month = self._windows(policy)
        headroom = self.settings.min_headroom_tokens
        for scope, who, limit, window in checks:
            if limit is None:
                continue
            since, reset = (day, next_day) if window == "day" else (month, next_month)
            used = self._used(
                site_id, policy, since=since, until=reset, user_id=user_id if who == 0 else None
            )
            if used + headroom > limit:
                return QuotaExceeded(scope, used, limit, reset), policy
        return None, policy

    def check(self, site_id: str, user_id: int, claim: QuotaClaim | None) -> None:
        """Refuse a new turn when less than the headroom remains."""
        problem, _ = self._evaluate(site_id, user_id, claim)
        if problem is not None:
            raise problem

    def turn_exhausted(
        self, site_id: str, user_id: int, claim: QuotaClaim | None, *, turn_tokens: int
    ) -> bool:
        """Whether a running turn must wrap up (per-turn cap or a window limit)."""
        policy = self.policy(site_id)
        cap = policy.max_tokens_per_turn or self.settings.default_max_tokens_per_turn
        if turn_tokens > cap:
            return True
        # Recorded usage already includes this turn's calls; stop when another model call
        # would no longer fit the headroom — the same rule that refuses a new turn.
        problem, _ = self._evaluate(site_id, user_id, claim)
        return problem is not None

    # -- metering -----------------------------------------------------------

    def record(
        self,
        site_id: str,
        user_id: int,
        conversation_id: str,
        turn_id: str,
        usage: UsageEvent,
        *,
        purpose: str = "chat",
    ) -> int:
        """Record one model call; returns the tokens it counts for."""
        self.store.record_usage(
            site_id,
            user_id=user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            purpose=purpose,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            estimated=usage.estimated,
            ts=int(self._clock().timestamp()),
        )
        counted = usage.input_tokens + usage.output_tokens
        if not self.policy(site_id).count_reasoning:
            counted -= usage.reasoning_tokens
        return counted

    def record_ingest(self, site_id: str, tokens: int, *, purpose: str = "embed") -> None:
        self.store.record_usage(
            site_id,
            user_id=None,
            conversation_id=None,
            turn_id=None,
            purpose=purpose,
            input_tokens=tokens,
            output_tokens=0,
            reasoning_tokens=0,
            estimated=True,
            ts=int(self._clock().timestamp()),
        )

    def ingest_allowed(self, site_id: str) -> bool:
        limit = self.ceilings(site_id).ingest_tokens_per_day
        if limit is None:
            return True
        policy = self.policy(site_id)
        day, next_day, _, _ = self._windows(policy)
        rows = self.store.usage_rows(site_id, since=day, until=next_day, purposes=INGEST_PURPOSES)
        return self._tokens(rows, policy) < limit

    # -- reporting ----------------------------------------------------------

    def status(self, site_id: str, user_id: int, claim: QuotaClaim | None) -> QuotaStatus:
        policy, checks = self._limits(site_id, claim)
        day, next_day, month, next_month = self._windows(policy)
        limits = {(s, w): limit for s, _, limit, w in checks}
        return QuotaStatus(
            used_today=self._used(site_id, policy, since=day, until=next_day, user_id=user_id),
            limit_today=limits.get(("user", "day")),
            used_month=self._used(site_id, policy, since=month, until=next_month, user_id=user_id),
            limit_month=limits.get(("user", "month")),
            resets_at=next_day,
            exempt=bool(claim and claim.exempt),
        )

    def report(
        self, site_id: str, *, period: Literal["day", "month"], by: Literal["user", "site"]
    ) -> list[dict[str, int]]:
        policy = self.policy(site_id)
        day, next_day, month, next_month = self._windows(policy)
        since, until = (day, next_day) if period == "day" else (month, next_month)
        rows = self.store.usage_rows(site_id, since=since, until=until, purposes=CHAT_PURPOSES)
        if by == "site":
            return [{"tokens": self._tokens(rows, policy), "calls": len(rows)}] if rows else []
        per_user: dict[int, list[Any]] = {}
        for r in rows:
            per_user.setdefault(int(r["user_id"]), []).append(r)
        report = [
            {"user_id": uid, "tokens": self._tokens(items, policy), "calls": len(items)}
            for uid, items in per_user.items()
        ]
        return sorted(report, key=lambda r: (-r["tokens"], r["user_id"]))
