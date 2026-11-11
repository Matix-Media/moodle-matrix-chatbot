"""Token quotas: per user, per site, with plugin policy, exemptions and API ceilings."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from moodle_llm_chat.access.scope import QuotaClaim
from moodle_llm_chat.agent.events import UsageEvent
from moodle_llm_chat.config import QuotaSection
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.quota.service import QuotaExceeded, QuotaPolicy, QuotaService, SiteCeilings

S = "site-a"
TZ = ZoneInfo("Europe/Berlin")


class Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> datetime:
        return self.when


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 11, 10, 12, 0, tzinfo=TZ))


@pytest.fixture
def quota(store: Store, clock: Clock) -> QuotaService:
    store.add_site(S, name="A", url="https://moodle.a.test")
    return QuotaService(
        store, QuotaSection(min_headroom_tokens=100), timezone="Europe/Berlin", clock=clock
    )


def use(q: QuotaService, user: int, tokens: int, *, reasoning: int = 0) -> None:
    q.record(
        S,
        user,
        "c",
        "t",
        UsageEvent(
            input_tokens=tokens // 2, output_tokens=tokens - tokens // 2, reasoning_tokens=reasoning
        ),
    )


class TestUserLimits:
    def test_within_limit_passes(self, quota: QuotaService) -> None:
        use(quota, 1, 500)
        quota.check(S, 1, QuotaClaim(day=1000))

    def test_headroom_refuses_before_any_call(self, quota: QuotaService) -> None:
        use(quota, 1, 950)
        with pytest.raises(QuotaExceeded) as err:
            quota.check(S, 1, QuotaClaim(day=1000))
        assert err.value.scope == "user"
        assert err.value.used == 950 and err.value.limit == 1000
        assert err.value.resets_at == int(datetime(2026, 11, 11, tzinfo=TZ).timestamp())

    def test_users_are_independent(self, quota: QuotaService) -> None:
        use(quota, 1, 2000)
        quota.check(S, 2, QuotaClaim(day=1000))

    def test_day_window_resets_at_local_midnight(self, quota: QuotaService, clock: Clock) -> None:
        use(quota, 1, 2000)
        clock.when = datetime(2026, 11, 11, 0, 5, tzinfo=TZ)
        quota.check(S, 1, QuotaClaim(day=1000))

    def test_month_limit(self, quota: QuotaService, clock: Clock) -> None:
        use(quota, 1, 900)
        clock.when = datetime(2026, 11, 20, tzinfo=TZ)
        use(quota, 1, 900)
        with pytest.raises(QuotaExceeded) as err:
            quota.check(S, 1, QuotaClaim(month=1800))
        assert err.value.resets_at == int(datetime(2026, 12, 1, tzinfo=TZ).timestamp())

    def test_site_policy_default_applies_without_claim(
        self, quota: QuotaService, store: Store
    ) -> None:
        quota.set_policy(S, QuotaPolicy(user_tokens_per_day=500))
        use(quota, 1, 450)
        with pytest.raises(QuotaExceeded):
            quota.check(S, 1, None)

    def test_claim_overrides_site_default(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(user_tokens_per_day=500))
        use(quota, 1, 450)
        quota.check(S, 1, QuotaClaim(day=5000))

    def test_exempt_user_skips_user_limits(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(user_tokens_per_day=10))
        use(quota, 1, 5000)
        quota.check(S, 1, QuotaClaim(day=10, exempt=True))

    def test_reasoning_can_be_excluded(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(count_reasoning=False))
        use(quota, 1, 1000, reasoning=400)
        assert quota.status(S, 1, QuotaClaim(day=10_000)).used_today == 600


class TestSiteLimits:
    def test_site_limit_counts_all_users(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=1000))
        use(quota, 1, 500)
        use(quota, 2, 450)
        with pytest.raises(QuotaExceeded) as err:
            quota.check(S, 3, None)
        assert err.value.scope == "site"

    def test_exempt_user_still_bound_by_site_unless_configured(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=1000))
        use(quota, 2, 1000)
        with pytest.raises(QuotaExceeded):
            quota.check(S, 1, QuotaClaim(exempt=True))
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=1000, exempt_users_bypass_site=True))
        quota.check(S, 1, QuotaClaim(exempt=True))

    def test_api_ceiling_cannot_be_raised_by_the_plugin(self, quota: QuotaService) -> None:
        quota.set_ceilings(S, SiteCeilings(tokens_per_day=800))
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=1_000_000))
        use(quota, 1, 750)
        with pytest.raises(QuotaExceeded) as err:
            quota.check(S, 2, QuotaClaim(exempt=True))
        assert err.value.limit == 800

    def test_ceiling_applies_even_with_bypass(self, quota: QuotaService) -> None:
        quota.set_ceilings(S, SiteCeilings(tokens_per_day=100))
        quota.set_policy(S, QuotaPolicy(exempt_users_bypass_site=True))
        use(quota, 1, 100)
        with pytest.raises(QuotaExceeded):
            quota.check(S, 1, QuotaClaim(exempt=True))

    def test_sites_are_isolated(self, quota: QuotaService, store: Store) -> None:
        store.add_site("site-b", name="B", url="https://b.test")
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=100))
        quota.record("site-b", 1, "c", "t", UsageEvent(input_tokens=5000))
        quota.check(S, 1, None)


class TestTurnBudget:
    def test_per_turn_cap(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(max_tokens_per_turn=1000))
        assert not quota.turn_exhausted(S, 1, None, turn_tokens=900)
        assert quota.turn_exhausted(S, 1, None, turn_tokens=1001)

    def test_default_turn_cap_from_settings(self, quota: QuotaService) -> None:
        assert quota.turn_exhausted(S, 1, None, turn_tokens=60_001)

    def test_window_exhausted_mid_turn(self, quota: QuotaService) -> None:
        use(quota, 1, 990)
        assert quota.turn_exhausted(S, 1, QuotaClaim(day=1000), turn_tokens=20)


class TestStatusAndReports:
    def test_status(self, quota: QuotaService) -> None:
        use(quota, 1, 300)
        status = quota.status(S, 1, QuotaClaim(day=1000, month=5000))
        assert (status.used_today, status.limit_today, status.used_month, status.limit_month) == (
            300,
            1000,
            300,
            5000,
        )
        assert not status.exempt

    def test_usage_report_by_user(self, quota: QuotaService) -> None:
        use(quota, 1, 300)
        use(quota, 2, 100)
        use(quota, 1, 50)
        rows = quota.report(S, period="day", by="user")
        assert rows == [
            {"user_id": 1, "tokens": 350, "calls": 2},
            {"user_id": 2, "tokens": 100, "calls": 1},
        ]
        assert quota.report(S, period="month", by="site") == [{"tokens": 450, "calls": 3}]

    def test_estimated_usage_is_flagged(self, quota: QuotaService) -> None:
        quota.record(S, 1, "c", "t", UsageEvent(output_tokens=40, estimated=True))
        assert quota.report(S, period="day", by="site")[0]["tokens"] == 40


class TestIngest:
    def test_ingest_ceiling(self, quota: QuotaService) -> None:
        quota.set_ceilings(S, SiteCeilings(ingest_tokens_per_day=1000))
        assert quota.ingest_allowed(S)
        quota.record_ingest(S, 1200)
        assert not quota.ingest_allowed(S)

    def test_ingest_usage_does_not_count_against_users(self, quota: QuotaService) -> None:
        quota.set_policy(S, QuotaPolicy(site_tokens_per_day=100))
        quota.record_ingest(S, 5000)
        quota.check(S, 1, None)
