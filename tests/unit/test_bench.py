"""Verifies spec 012 — RAG method benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest

from bsbot.api.index.search import SearchHit
from bsbot.eval.golden import GoldenQuestion, GoldenSetError, load_golden_set
from bsbot.eval.metrics import (
    answerable_correct,
    keyword_coverage,
    reciprocal_rank,
    retrieval_hit,
)
from bsbot.eval.runner import PRESETS, run_benchmark


def hit(chunk_id: int, doc_id: str, text: str = "Inhalt.", **kw) -> SearchHit:
    defaults = dict(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        header_text="LF05 › Sec › Mod",
        course_name="LF05IT",
        module_name="Skript",
        module_url=f"https://m.example/mod/page/view.php?id={chunk_id}",
        title="Skript",
        page=None,
        score=0.5,
        sources=["keyword"],
    )
    return SearchHit(**{**defaults, **kw})


class FakeSearcher:
    def __init__(
        self,
        hits_by_query: dict[str, list[SearchHit]] | None = None,
        *,
        default: list[SearchHit] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._hits_by_query = hits_by_query or {}
        self._default = default or []
        self._fail_on = fail_on or set()
        self.queries: list[str] = []
        self.calls = 0

    def search(self, query: str, *, limit: int = 12, room_id: str | None = None) -> list[SearchHit]:
        self.calls += 1
        self.queries.append(query)
        if query in self._fail_on:
            raise RuntimeError("search backend unavailable")
        return self._hits_by_query.get(query, self._default)[:limit]

    def neighbors(self, chunk_id: int, *, radius: int) -> list[SearchHit]:
        return []


class FakeLLM:
    def __init__(self, responses: dict[str, str] | None = None, fail: set[str] | None = None):
        self._responses = responses or {}
        self._fail = fail or set()
        self.prompts: list[tuple[str, str]] = []

    def generate(
        self, prompt: str, *, system: str | None = None, purpose: str = "answer", **kw
    ) -> str:
        self.prompts.append((purpose, prompt))
        if purpose in self._fail:
            raise RuntimeError("quota exceeded")
        return self._responses.get(purpose, "Die Prüfung ist am 15.03.2026. [1]")


class TestGoldenLoading:
    def test_minimal_entry_gets_defaults(self, tmp_path: Path) -> None:
        """AC-1: only id and question are required."""
        path = tmp_path / "golden.yaml"
        path.write_text("- id: q1\n  question: Wann ist die Prüfung?\n")
        questions = load_golden_set(path)
        assert len(questions) == 1
        q = questions[0]
        assert q.answerable is True
        assert q.expected_doc_ids == []
        assert q.has_retrieval_target is False

    def test_missing_required_field_names_the_entry(self, tmp_path: Path) -> None:
        """AC-2: a malformed entry fails to load with a named error, not a raw traceback."""
        path = tmp_path / "golden.yaml"
        path.write_text("- id: broken-one\n  category: oops\n")  # no `question`
        with pytest.raises(GoldenSetError, match="broken-one"):
            load_golden_set(path)

    def test_duplicate_id_rejected(self, tmp_path: Path) -> None:
        """AC-3"""
        path = tmp_path / "golden.yaml"
        path.write_text("- id: dup\n  question: A?\n- id: dup\n  question: B?\n")
        with pytest.raises(GoldenSetError, match="dup"):
            load_golden_set(path)

    def test_empty_file_yields_no_questions(self, tmp_path: Path) -> None:
        path = tmp_path / "golden.yaml"
        path.write_text("")
        assert load_golden_set(path) == []

    def test_not_a_list_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "golden.yaml"
        path.write_text("question: not a list\n")
        with pytest.raises(GoldenSetError):
            load_golden_set(path)

    def test_full_entry_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "golden.yaml"
        path.write_text(
            "- id: full\n"
            "  question: Wann ist die Prüfung?\n"
            "  answerable: true\n"
            "  expected_doc_ids: ['1:2:0']\n"
            "  expected_header_contains: ['Prüfung']\n"
            "  expected_keywords: ['15.03.2026']\n"
            "  category: termine\n"
        )
        [q] = load_golden_set(path)
        assert q.expected_doc_ids == ["1:2:0"]
        assert q.has_retrieval_target is True


class TestMetrics:
    def _q(self, **kw) -> GoldenQuestion:
        return GoldenQuestion(id="q", question="Frage?", **kw)

    def test_retrieval_hit_by_doc_id(self) -> None:
        q = self._q(expected_doc_ids=["d2"])
        assert retrieval_hit(q, [("d1", ""), ("d2", "")]) is True
        assert retrieval_hit(q, [("d1", "")]) is False

    def test_retrieval_hit_by_header_substring_case_insensitive(self) -> None:
        q = self._q(expected_header_contains=["Lernfeld 10"])
        assert retrieval_hit(q, [("d1", "LF10 › lernfeld 10 › Blockplan")]) is True
        assert retrieval_hit(q, [("d1", "Lernfeld 4 › Blockplan")]) is False

    def test_retrieval_hit_none_without_a_target(self) -> None:
        """AC-10: a keyword-only entry never counts as a retrieval miss."""
        q = self._q(expected_keywords=["irgendwas"])
        assert retrieval_hit(q, [("d1", "")]) is None

    def test_reciprocal_rank(self) -> None:
        q = self._q(expected_doc_ids=["d3"])
        assert reciprocal_rank(q, [("d1", ""), ("d2", ""), ("d3", "")]) == pytest.approx(1 / 3)

    def test_reciprocal_rank_zero_when_absent(self) -> None:
        q = self._q(expected_doc_ids=["d9"])
        assert reciprocal_rank(q, [("d1", ""), ("d2", "")]) == 0.0

    def test_reciprocal_rank_none_without_a_target(self) -> None:
        assert reciprocal_rank(self._q(), [("d1", "")]) is None

    def test_keyword_coverage_fraction(self) -> None:
        q = self._q(expected_keywords=["15.03.2026", "Anmeldung"])
        assert keyword_coverage(q, "Die Prüfung ist am 15.03.2026.") == pytest.approx(0.5)

    def test_keyword_coverage_none_without_keywords(self) -> None:
        assert keyword_coverage(self._q(), "irgendein Text") is None

    def test_answerable_correct(self) -> None:
        assert answerable_correct(self._q(answerable=True), grounded=True) is True
        assert answerable_correct(self._q(answerable=True), grounded=False) is False
        assert answerable_correct(self._q(answerable=False), grounded=False) is True


class TestRunner:
    def test_baseline_preset_issues_exactly_one_query_per_question(self) -> None:
        golden = [GoldenQuestion(id="q1", question="Wann ist die Prüfung?")]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm, presets={"baseline": PRESETS["baseline"]})
        assert searcher.queries == ["Wann ist die Prüfung?"]
        assert report.presets[0].aggregate.n == 1

    def test_presets_reuse_the_same_searcher_and_llm(self) -> None:
        """AC-12: no re-index/re-embed between presets — same instances throughout."""
        golden = [GoldenQuestion(id="q1", question="Frage?")]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM()
        run_benchmark(
            golden,
            searcher,
            llm,
            presets={"baseline": PRESETS["baseline"], "expand": PRESETS["expand"]},
        )
        # Both presets' queries landed on the one shared FakeSearcher instance.
        assert searcher.calls >= 2

    def test_retrieval_hit_final_reflects_the_actual_context(self) -> None:
        """AC-4: bounded by max_context_chunks, so a low-ranked expected doc misses."""
        golden = [GoldenQuestion(id="q1", question="Frage?", expected_doc_ids=["d5"])]
        hits = [hit(i, f"d{i}") for i in range(1, 10)]
        searcher = FakeSearcher(default=hits)
        llm = FakeLLM()
        report = run_benchmark(
            golden,
            searcher,
            llm,
            presets={"baseline": PRESETS["baseline"]},
            pipeline_kwargs={"max_context_chunks": 2},
        )
        result = report.presets[0].results[0]
        assert result.retrieval_hit_final is False  # d5 is outside the top-2 context
        assert result.retrieval_hit_any is True  # but it was returned by the searcher

    def test_expansion_finds_what_baseline_misses(self) -> None:
        """The point of query expansion: retrieval-any should differ between presets."""
        golden = [
            GoldenQuestion(id="q1", question="wann is die prüfung", expected_doc_ids=["target"])
        ]
        searcher = FakeSearcher(
            hits_by_query={"Termin Abschlussprüfung": [hit(9, "target")]},
            default=[hit(1, "unrelated")],
        )
        llm = FakeLLM({"expand": "Termin Abschlussprüfung"})
        report = run_benchmark(
            golden,
            searcher,
            llm,
            presets={"baseline": PRESETS["baseline"], "expand": PRESETS["expand"]},
        )
        by_preset = {pr.preset: pr.results[0] for pr in report.presets}
        assert by_preset["baseline"].retrieval_hit_any is False
        assert by_preset["expand"].retrieval_hit_any is True

    def test_unanswerable_question_measures_refusal_not_retrieval(self) -> None:
        golden = [GoldenQuestion(id="q1", question="Wie ist das Wetter?", answerable=False)]
        searcher = FakeSearcher(default=[])
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm, presets={"crag": PRESETS["crag"]})
        result = report.presets[0].results[0]
        assert result.answerable_correct is True
        assert result.retrieval_hit_final is None
        # crag=True adds the actionable Moodle-search-link fallback even with zero hits.
        assert result.actionable_fallback is True

    def test_keyword_coverage_not_scored_against_a_refusal(self) -> None:
        """Regression: the CRAG actionable-fallback link embeds the original
        question verbatim as a Moodle search URL, so a keyword drawn from the
        question's own topic can appear in a *refusal* purely because the refusal
        echoes the question — not because anything was actually answered."""
        golden = [
            GoldenQuestion(
                id="q1",
                question="Was ist ein Arbeitszeugnis?",
                expected_keywords=["Arbeitszeugnis"],
            )
        ]
        searcher = FakeSearcher(default=[])  # no hits at all -> refusal
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm, presets={"crag": PRESETS["crag"]})
        result = report.presets[0].results[0]
        assert result.answerable_correct is False  # refused an answerable question
        assert result.keyword_coverage is None

    def test_question_failure_is_isolated_and_recorded(self) -> None:
        """AC-15: one failing question does not abort the rest of the run."""
        golden = [
            GoldenQuestion(id="ok", question="Gute Frage?"),
            GoldenQuestion(id="boom", question="Kaputte Frage?"),
        ]
        searcher = FakeSearcher(default=[hit(1, "d1")], fail_on={"Kaputte Frage?"})
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm, presets={"baseline": PRESETS["baseline"]})
        results = {r.question_id: r for r in report.presets[0].results}
        assert results["ok"].error is None
        assert "unavailable" in (results["boom"].error or "")
        assert report.presets[0].aggregate.n_errors == 1
        assert report.presets[0].aggregate.n == 2

    def test_judge_score_parsed_from_llm_response(self) -> None:
        golden = [GoldenQuestion(id="q1", question="Frage?", expected_keywords=["15.03.2026"])]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM({"judge": "4 - gut, aber unvollständig"})
        report = run_benchmark(
            golden, searcher, llm, presets={"baseline": PRESETS["baseline"]}, judge=True
        )
        assert report.presets[0].results[0].judge_score == 4.0

    def test_judge_not_scored_when_disabled(self) -> None:
        golden = [GoldenQuestion(id="q1", question="Frage?")]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm, presets={"baseline": PRESETS["baseline"]})
        assert report.presets[0].results[0].judge_score is None
        assert not any(p == "judge" for p, _ in llm.prompts)

    def test_report_serializes_to_json_and_table(self) -> None:
        golden = [GoldenQuestion(id="q1", question="Frage?")]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM()
        report = run_benchmark(
            golden,
            searcher,
            llm,
            presets={"baseline": PRESETS["baseline"], "expand": PRESETS["expand"]},
        )
        table = report.to_table()
        assert "baseline" in table
        assert "expand" in table
        payload = report.to_json()
        assert '"preset": "baseline"' in payload

    def test_every_preset_name_maps_to_valid_pipeline_flags(self) -> None:
        """Every preset must actually be a usable AnswerPipeline kwargs dict."""
        golden = [GoldenQuestion(id="q1", question="Frage?")]
        searcher = FakeSearcher(default=[hit(1, "d1")])
        llm = FakeLLM()
        report = run_benchmark(golden, searcher, llm)  # runs every PRESETS entry
        assert {pr.preset for pr in report.presets} == set(PRESETS)
        assert all(pr.aggregate.n_errors == 0 for pr in report.presets)
