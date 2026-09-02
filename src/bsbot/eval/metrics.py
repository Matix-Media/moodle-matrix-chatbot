"""Pure metric functions — see ``specs/012-benchmarks.md`` AC-4..AC-9.

Kept free of the pipeline and the runner so each metric is independently testable
against plain lists/strings, not fixtures wired through ``AnswerPipeline``.
"""

from __future__ import annotations

from bsbot.eval.golden import GoldenQuestion


def matches_expected(question: GoldenQuestion, doc_id: str, header_text: str) -> bool:
    """Whether one retrieved document satisfies this question's retrieval target."""
    if doc_id in question.expected_doc_ids:
        return True
    header_lower = header_text.lower()
    return any(needle.lower() in header_lower for needle in question.expected_header_contains)


def retrieval_hit(question: GoldenQuestion, ranked_docs: list[tuple[str, str]]) -> bool | None:
    """AC-4/AC-5: whether an expected document appears anywhere in ``ranked_docs``.

    ``ranked_docs`` is a list of ``(doc_id, header_text)`` in rank order. Returns
    ``None`` (not scored — AC-10) when the question gives no retrieval target at all.
    """
    if not question.has_retrieval_target:
        return None
    return any(matches_expected(question, doc_id, header) for doc_id, header in ranked_docs)


def reciprocal_rank(question: GoldenQuestion, ranked_docs: list[tuple[str, str]]) -> float | None:
    """AC-6: 1/rank of the first matching document; 0.0 if none matched.

    ``None`` (not scored) when the question gives no retrieval target — same
    AC-10 exemption as ``retrieval_hit``.
    """
    if not question.has_retrieval_target:
        return None
    for rank, (doc_id, header) in enumerate(ranked_docs, start=1):
        if matches_expected(question, doc_id, header):
            return 1.0 / rank
    return 0.0


def keyword_coverage(question: GoldenQuestion, answer_text: str) -> float | None:
    """AC-8: fraction of expected_keywords present (case-insensitive) in answer_text.

    ``None`` when the question gives no keywords, so it doesn't drag down an
    aggregate it was never meant to feed.
    """
    if not question.expected_keywords:
        return None
    text_lower = answer_text.lower()
    found = sum(1 for kw in question.expected_keywords if kw.lower() in text_lower)
    return found / len(question.expected_keywords)


def answerable_correct(question: GoldenQuestion, grounded: bool) -> bool:
    """AC-7: whether Answer.grounded matches the question's answerable expectation."""
    return grounded == question.answerable


__all__ = [
    "answerable_correct",
    "keyword_coverage",
    "matches_expected",
    "reciprocal_rank",
    "retrieval_hit",
]
