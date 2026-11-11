"""Persisted job queue backing the processing pipeline."""

from __future__ import annotations

from moodle_llm_chat.index.store import Store

S = "site-a"


class TestEnqueueClaim:
    def test_claim_marks_running_and_respects_limit(self, store: Store) -> None:
        for i in range(3):
            store.enqueue_job(S, f"d{i}", "extract", now=100)
        claimed = store.claim_jobs("extract", limit=2, now=100)
        assert [j.doc_id for j in claimed] == ["d0", "d1"]
        assert all(j.attempts == 0 for j in claimed)
        assert [j.doc_id for j in store.claim_jobs("extract", limit=5, now=100)] == ["d2"]
        assert store.claim_jobs("extract", limit=5, now=100) == []

    def test_stages_are_independent(self, store: Store) -> None:
        store.enqueue_job(S, "d", "fetch", now=1)
        store.enqueue_job(S, "d", "extract", now=1)
        assert [j.stage for j in store.claim_jobs("fetch", limit=5, now=1)] == ["fetch"]
        assert [j.stage for j in store.claim_jobs("extract", limit=5, now=1)] == ["extract"]

    def test_priority_orders_claims(self, store: Store) -> None:
        store.enqueue_job(S, "bulk", "extract", priority=1, now=1)
        store.enqueue_job(S, "urgent", "extract", priority=0, now=2)
        assert [j.doc_id for j in store.claim_jobs("extract", limit=1, now=3)] == ["urgent"]

    def test_reenqueue_resets_state_and_keeps_best_priority(self, store: Store) -> None:
        store.enqueue_job(S, "d", "extract", priority=0, now=1)
        (job,) = store.claim_jobs("extract", limit=1, now=1)
        store.finish_job(job.id, state="done", now=2)
        store.enqueue_job(S, "d", "extract", priority=1, now=3)
        (again,) = store.claim_jobs("extract", limit=1, now=3)
        assert again.id == job.id
        assert again.priority == 1
        assert again.attempts == 0

    def test_enqueue_while_queued_keeps_lower_priority_value(self, store: Store) -> None:
        store.enqueue_job(S, "d", "extract", priority=1, now=1)
        store.enqueue_job(S, "d", "extract", priority=0, now=1)
        (job,) = store.claim_jobs("extract", limit=1, now=1)
        assert job.priority == 0


class TestRetry:
    def test_retry_backs_off_until_due(self, store: Store) -> None:
        store.enqueue_job(S, "d", "fetch", now=0)
        (job,) = store.claim_jobs("fetch", limit=1, now=0)
        store.retry_job(job.id, delay_s=30, error="timeout", max_attempts=3, now=0)
        assert store.claim_jobs("fetch", limit=1, now=29) == []
        (again,) = store.claim_jobs("fetch", limit=1, now=30)
        assert again.attempts == 1
        assert again.last_error == "timeout"

    def test_exhausted_retries_fail_the_job(self, store: Store) -> None:
        store.enqueue_job(S, "d", "fetch", now=0)
        for attempt in range(3):
            (job,) = store.claim_jobs("fetch", limit=1, now=attempt * 100)
            store.retry_job(job.id, delay_s=1, error="boom", max_attempts=3, now=attempt * 100)
        assert store.claim_jobs("fetch", limit=1, now=10_000) == []
        counts = store.job_counts(now=10_000)
        assert counts["fetch"]["failed"] == 1


class TestRecovery:
    def test_running_jobs_are_requeued_on_startup(self, store: Store) -> None:
        store.enqueue_job(S, "d", "extract", now=0)
        store.claim_jobs("extract", limit=1, now=0)
        assert store.reset_running_jobs() == 1
        assert [j.doc_id for j in store.claim_jobs("extract", limit=1, now=0)] == ["d"]

    def test_counts_and_oldest_age(self, store: Store) -> None:
        store.enqueue_job(S, "a", "extract", now=100)
        store.enqueue_job(S, "b", "extract", now=150)
        counts = store.job_counts(now=200)
        assert counts["extract"]["queued"] == 2
        assert counts["extract"]["oldest_queued_age_s"] == 100

    def test_has_pending_work(self, store: Store) -> None:
        assert not store.has_due_jobs(now=0)
        store.enqueue_job(S, "a", "extract", now=10)
        assert not store.has_due_jobs(now=5)
        assert store.has_due_jobs(now=10)
        assert store.next_job_due_at() == 10
