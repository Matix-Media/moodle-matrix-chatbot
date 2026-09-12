"""Benchmark runner — see ``specs/012-benchmarks.md``.

Runs the same golden question set through the pipeline once per named preset,
instrumenting the searcher and LLM to recover retrieval- and cost-metrics without
changing what the pipeline itself does. Index-time techniques (HyPE, semantic
chunking) are out of scope here (AC-13): compare them by pointing this runner at two
differently-built ``index.db`` files with the same preset, across two invocations.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import structlog
from pydantic import BaseModel

from bsbot.eval.golden import GoldenQuestion
from bsbot.eval.metrics import answerable_correct, keyword_coverage, reciprocal_rank, retrieval_hit
from bsbot.index.search import SearchHit
from bsbot.pii.tokenizer import PiiTokenizer
from bsbot.rag.pipeline import AnswerPipeline, LLMLike, SearcherLike
from bsbot.rag.prompts import JUDGE_TEMPLATE

log = structlog.get_logger(__name__)

#: Every optional technique off — the floor every preset is measured against.
_BASELINE_FLAGS: dict[str, bool] = {
    "expand": False,
    "rerank": False,
    "followup": False,
    "decompose": False,
    "step_back": False,
    "compress_context": False,
    "crag": False,
    "dated_rerank": False,
    "crag_filter": False,
    "suggest_followup": False,
}

#: Named presets (AC-11): baseline, one technique at a time, then everything on.
#: `suggest_followup` is left off everywhere — it only adds suggested_questions and
#: never affects retrieval or the answer text these metrics measure, so enabling it
#: would just spend an extra LLM call with no effect on any recorded metric.
PRESETS: dict[str, dict[str, bool]] = {
    "baseline": dict(_BASELINE_FLAGS),
    "expand": {**_BASELINE_FLAGS, "expand": True},
    "rerank": {**_BASELINE_FLAGS, "rerank": True},
    # The follow-up hop only ever fires when rerank is on (see pipeline.py); a
    # standalone "followup" preset would be indistinguishable from "rerank".
    "rerank_followup": {**_BASELINE_FLAGS, "rerank": True, "followup": True},
    "decompose": {**_BASELINE_FLAGS, "decompose": True},
    "step_back": {**_BASELINE_FLAGS, "step_back": True},
    "compress": {**_BASELINE_FLAGS, "compress_context": True},
    "crag": {**_BASELINE_FLAGS, "crag": True},
    # dated_rerank only takes effect with rerank on (see AnswerPipeline docstring).
    "dated_rerank": {**_BASELINE_FLAGS, "rerank": True, "dated_rerank": True},
    "crag_filter": {**_BASELINE_FLAGS, "crag_filter": True},
    "dated_rerank_crag_filter": {
        **_BASELINE_FLAGS,
        "rerank": True,
        "dated_rerank": True,
        "crag_filter": True,
    },
    "kitchen_sink": {
        "expand": True,
        "rerank": True,
        "followup": True,
        "decompose": True,
        "step_back": True,
        "compress_context": True,
        "crag": True,
        "dated_rerank": False,
        "crag_filter": False,
        "suggest_followup": False,
    },
    # Everything that showed a clear, isolated win on this corpus's first run —
    # expand (answerable-accuracy), the two rerank fixes — deliberately excluding
    # plain rerank and step_back, which regressed date-relative questions on their
    # own. Compare against kitchen_sink to see whether informed selection beats
    # "everything on".
    "best_guess": {
        **_BASELINE_FLAGS,
        "expand": True,
        "rerank": True,
        "dated_rerank": True,
        "crag_filter": True,
        "crag": True,
    },
    # Isolates whether crag_filter is what breaks best_guess, or expand's wider
    # candidate pool alone does — same as best_guess minus crag_filter.
    "expand_dated_rerank": {
        **_BASELINE_FLAGS,
        "expand": True,
        "rerank": True,
        "dated_rerank": True,
        "crag": True,
    },
}


class QuestionResult(BaseModel):
    question_id: str
    category: str | None = None
    #: Set when answering this question raised; every other field stays None/0 (AC-15).
    error: str | None = None
    retrieval_hit_final: bool | None = None
    retrieval_hit_any: bool | None = None
    mrr: float | None = None
    answerable_correct: bool | None = None
    keyword_coverage: float | None = None
    #: Only scored for a correctly-refused, unanswerable question — whether the
    #: refusal carried the CRAG actionable fallback link.
    actionable_fallback: bool | None = None
    judge_score: float | None = None
    latency_ms: float = 0.0
    llm_calls: int = 0
    llm_chars: int = 0


class PresetAggregate(BaseModel):
    n: int
    n_errors: int
    retrieval_hit_final_rate: float | None = None
    retrieval_hit_any_rate: float | None = None
    mean_mrr: float | None = None
    answerable_accuracy: float | None = None
    mean_keyword_coverage: float | None = None
    actionable_fallback_rate: float | None = None
    mean_judge_score: float | None = None
    mean_latency_ms: float = 0.0
    mean_llm_calls: float = 0.0
    mean_llm_chars: float = 0.0


class PresetReport(BaseModel):
    preset: str
    results: list[QuestionResult]
    aggregate: PresetAggregate


class BenchmarkReport(BaseModel):
    presets: list[PresetReport]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def to_table(self) -> str:
        """A plain-text comparison table for terminal output."""
        headers = [
            "preset",
            "n",
            "err",
            "ret@final",
            "ret@any",
            "mrr",
            "answ_acc",
            "kw_cov",
            "fallback",
            "judge",
            "calls",
            "chars",
            "ms",
        ]
        rows = [headers]
        for pr in self.presets:
            a = pr.aggregate
            rows.append(
                [
                    pr.preset,
                    str(a.n),
                    str(a.n_errors),
                    _fmt(a.retrieval_hit_final_rate),
                    _fmt(a.retrieval_hit_any_rate),
                    _fmt(a.mean_mrr),
                    _fmt(a.answerable_accuracy),
                    _fmt(a.mean_keyword_coverage),
                    _fmt(a.actionable_fallback_rate),
                    _fmt(a.mean_judge_score),
                    f"{a.mean_llm_calls:.1f}",
                    f"{a.mean_llm_chars:.0f}",
                    f"{a.mean_latency_ms:.0f}",
                ]
            )
        widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
        lines = [
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
            for row in rows
        ]
        return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


class _RecordingSearcher:
    """Wraps a SearcherLike, recording every hit any query returned (AC-5, AC-4)."""

    def __init__(self, inner: SearcherLike) -> None:
        self._inner = inner
        self.recorded: list[SearchHit] = []

    def search(
        self,
        query: str,
        *,
        limit: int = 12,
        room_id: str | None = None,
        lexical_query: str | None = None,
        target_date: date | None = None,
    ) -> list[SearchHit]:
        # Degrade kwarg by kwarg on TypeError, same as AnswerPipeline._search_one:
        # a golden-set run's own SearcherLike double may predate `target_date`,
        # `lexical_query`, or `room_id` without that being a reason to fail every
        # question.
        kwargs: dict[str, object] = {
            "limit": limit,
            "room_id": room_id,
            "lexical_query": lexical_query,
            "target_date": target_date,
        }
        while True:
            try:
                hits = self._inner.search(query, **kwargs)  # type: ignore[arg-type]
                break
            except TypeError:
                if "target_date" in kwargs:
                    del kwargs["target_date"]
                elif "lexical_query" in kwargs:
                    del kwargs["lexical_query"]
                elif "room_id" in kwargs:
                    del kwargs["room_id"]
                else:
                    raise
        self.recorded.extend(hits)
        return hits

    def neighbors(self, chunk_id: int, *, radius: int) -> list[SearchHit]:
        return self._inner.neighbors(chunk_id, radius=radius)

    def reset(self) -> None:
        self.recorded = []


class _RecordingLLM:
    """Wraps an LLMLike, counting calls and prompt+response characters (AC-9)."""

    def __init__(self, inner: LLMLike) -> None:
        self._inner = inner
        self.calls = 0
        self.chars = 0

    def generate(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        self.chars += len(prompt) + len(kwargs.get("system") or "")
        result = self._inner.generate(prompt, **kwargs)
        self.chars += len(result)
        return result

    def reset(self) -> None:
        self.calls = 0
        self.chars = 0


def _judge_score(
    llm: LLMLike,
    question: GoldenQuestion,
    answer_text: str,
    *,
    pii_tokenizer: PiiTokenizer | None = None,
) -> float | None:
    """Coarse 1-5 LLM-judge relevance rating (Non-goals: not a metric substitute).

    This call sends text to Gemini *after* the pipeline's own protection has
    ended: the golden question and its keywords are authored text the pipeline
    never saw, and ``answer_text`` has already had every token resolved back to a
    real value by ``_finalise``. Both must be tokenized again here.
    """
    keywords = ", ".join(question.expected_keywords) or "(keine vorgegeben)"
    question_text = question.question
    if pii_tokenizer is not None:
        question_text = pii_tokenizer.tokenize(question_text)
        keywords = pii_tokenizer.tokenize(keywords)
        answer_text = pii_tokenizer.tokenize(answer_text)
    prompt = JUDGE_TEMPLATE.format(
        question=question_text,
        expected_keywords=keywords,
        answer=answer_text,
    )
    try:
        raw = llm.generate(prompt, purpose="judge", temperature=0.0)
    except Exception as exc:
        log.info("bench.judge_failed", question=question.id, error=str(exc))
        return None
    match = re.search(r"[1-5]", raw)
    return float(match.group(0)) if match else None


def run_benchmark(
    golden: list[GoldenQuestion],
    searcher: SearcherLike,
    llm: LLMLike,
    *,
    presets: dict[str, dict[str, bool]] | None = None,
    judge: bool = False,
    pipeline_kwargs: dict[str, Any] | None = None,
) -> BenchmarkReport:
    """Run every preset over the whole golden set (AC-11, AC-12).

    ``searcher`` and ``llm`` are reused, unmodified, across every preset — only the
    pipeline's own optional-technique flags change between runs.
    """
    presets = PRESETS if presets is None else presets
    base_kwargs = dict(pipeline_kwargs or {})
    # The judge call happens outside the pipeline, so it needs the tokenizer the
    # pipeline was configured with (spec 013 AC-15's protection stops at the
    # pipeline boundary).
    judge_tokenizer: PiiTokenizer | None = base_kwargs.get("pii_tokenizer")

    preset_reports: list[PresetReport] = []
    for name, flags in presets.items():
        recording_searcher = _RecordingSearcher(searcher)
        recording_llm = _RecordingLLM(llm)
        kwargs: dict[str, Any] = {**flags, **base_kwargs}
        pipeline = AnswerPipeline(recording_searcher, recording_llm, **kwargs)

        results: list[QuestionResult] = []
        for gq in golden:
            recording_searcher.reset()
            recording_llm.reset()
            start = time.perf_counter()
            try:
                answer = pipeline.answer(gq.question)
            except Exception as exc:  # AC-15
                log.info("bench.question_failed", preset=name, question=gq.id, error=str(exc))
                results.append(
                    QuestionResult(question_id=gq.id, category=gq.category, error=str(exc))
                )
                continue
            elapsed_ms = (time.perf_counter() - start) * 1000

            header_by_doc = {h.doc_id: h.header_text for h in recording_searcher.recorded}
            final_ranked = [
                (doc_id, header_by_doc.get(doc_id, "")) for doc_id in answer.context_doc_ids
            ]
            any_ranked = [(h.doc_id, h.header_text) for h in recording_searcher.recorded]

            judge_score = (
                _judge_score(llm, gq, answer.text, pii_tokenizer=judge_tokenizer)
                if judge and answer.grounded
                else None
            )
            fallback = (
                ("🔍" in answer.text) if (not gq.answerable and not answer.grounded) else None
            )

            results.append(
                QuestionResult(
                    question_id=gq.id,
                    category=gq.category,
                    retrieval_hit_final=retrieval_hit(gq, final_ranked),
                    retrieval_hit_any=retrieval_hit(gq, any_ranked),
                    mrr=reciprocal_rank(gq, final_ranked),
                    answerable_correct=answerable_correct(gq, answer.grounded),
                    # Only scored on a grounded answer (AC-8): a refusal's fallback
                    # text can spuriously contain expected_keywords anyway — the
                    # CRAG actionable-fallback link embeds the original question
                    # verbatim (as a Moodle search URL), so a keyword drawn from the
                    # question's own topic can appear there even though the pipeline
                    # never actually answered.
                    keyword_coverage=keyword_coverage(gq, answer.text) if answer.grounded else None,
                    actionable_fallback=fallback,
                    judge_score=judge_score,
                    latency_ms=elapsed_ms,
                    llm_calls=recording_llm.calls,
                    llm_chars=recording_llm.chars,
                )
            )
        preset_reports.append(
            PresetReport(preset=name, results=results, aggregate=_aggregate(results))
        )

    return BenchmarkReport(presets=preset_reports)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate(results: list[QuestionResult]) -> PresetAggregate:
    ok = [r for r in results if r.error is None]

    def rates(attr: str) -> float | None:
        values = [getattr(r, attr) for r in ok if getattr(r, attr) is not None]
        return _mean([1.0 if v else 0.0 for v in values])

    def means(attr: str) -> float | None:
        values = [getattr(r, attr) for r in ok if getattr(r, attr) is not None]
        return _mean(values)

    return PresetAggregate(
        n=len(results),
        n_errors=len(results) - len(ok),
        retrieval_hit_final_rate=rates("retrieval_hit_final"),
        retrieval_hit_any_rate=rates("retrieval_hit_any"),
        mean_mrr=means("mrr"),
        answerable_accuracy=rates("answerable_correct"),
        mean_keyword_coverage=means("keyword_coverage"),
        actionable_fallback_rate=rates("actionable_fallback"),
        mean_judge_score=means("judge_score"),
        mean_latency_ms=_mean([r.latency_ms for r in ok]) or 0.0,
        mean_llm_calls=_mean([float(r.llm_calls) for r in ok]) or 0.0,
        mean_llm_chars=_mean([float(r.llm_chars) for r in ok]) or 0.0,
    )


__all__ = [
    "PRESETS",
    "BenchmarkReport",
    "PresetAggregate",
    "PresetReport",
    "QuestionResult",
    "run_benchmark",
]
