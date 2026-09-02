"""Golden question set — see ``specs/012-benchmarks.md`` AC-1..AC-3.

A YAML list of questions with everything optional except ``id`` and ``question``, so
a set can mix precise entries (naming the exact expected document) with fuzzy ones
(only keywords, or only an answerable/unanswerable expectation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class GoldenQuestion(BaseModel):
    model_config = {"frozen": True}

    id: str
    question: str
    #: Whether the corpus is expected to contain an answer at all. A question testing
    #: correct refusal (out-of-scope, not-in-Moodle) sets this to False.
    answerable: bool = True
    #: doc_id values (as produced by the crawler/manifest) that would satisfy this
    #: question if retrieved. Optional — a set can rely on expected_header_contains or
    #: expected_keywords alone instead.
    expected_doc_ids: list[str] = Field(default_factory=list)
    #: Case-insensitive substrings to match against a hit's header_text, for entries
    #: where the exact doc_id isn't known ahead of time.
    expected_header_contains: list[str] = Field(default_factory=list)
    #: Case-insensitive substrings expected in the final answer text (a date, a name,
    #: a number) — cheap proxy for "did the answer actually contain the right fact".
    expected_keywords: list[str] = Field(default_factory=list)
    #: Free-form grouping label for report breakdowns (e.g. "lexical-needle",
    #: "casual-phrasing", "temporal", "unanswerable"). Not interpreted by the runner.
    category: str | None = None
    notes: str | None = None

    @field_validator("id", "question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @property
    def has_retrieval_target(self) -> bool:
        return bool(self.expected_doc_ids or self.expected_header_contains)


class GoldenSetError(ValueError):
    """The golden question file is malformed."""


def load_golden_set(path: Path) -> list[GoldenQuestion]:
    """Load a golden question set (AC-1..AC-3). Raises GoldenSetError on malformed input."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GoldenSetError(f"{path}: expected a YAML list of questions, got {type(raw).__name__}")

    questions: list[GoldenQuestion] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise GoldenSetError(
                f"{path}: entry {index} must be a mapping, got {type(entry).__name__}"
            )
        try:
            question = GoldenQuestion(**entry)
        except Exception as exc:
            entry_id = entry.get("id", f"<entry {index}>")
            raise GoldenSetError(f"{path}: entry {entry_id!r} is invalid: {exc}") from exc
        if question.id in seen_ids:
            raise GoldenSetError(f"{path}: duplicate question id {question.id!r}")
        seen_ids.add(question.id)
        questions.append(question)
    return questions


__all__ = ["GoldenQuestion", "GoldenSetError", "load_golden_set"]
