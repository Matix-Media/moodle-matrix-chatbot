"""Human-maintained aliases for confusingly-named Moodle documents.

Some documents are named after institutional context with zero lexical or semantic
connection to what a student would actually search for — a Lernfeld 10 grading
sheet titled "Bewertung Barcamp" because the LF10 project (a Design Pattern
workshop) is presented at an event called a Barcamp. No retrieval mechanism —
BM25, embeddings, query expansion drawing on general knowledge — can discover that
association from the text, because it isn't *in* the text. It has to be told once,
the same way students eventually had to ask a teacher. This file is that "telling".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_aliases(path: Path) -> dict[str, list[str]]:
    """Load doc_id -> [alias phrases]. A missing file is not an error (AC-21)."""
    if not path.exists():
        return {}

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: expected a mapping of doc_id -> [aliases], got {type(raw).__name__}"
        )

    result: dict[str, list[str]] = {}
    for doc_id, aliases in raw.items():
        if not isinstance(aliases, list):
            raise ValueError(
                f"{path}: aliases for {doc_id!r} must be a list, got {type(aliases).__name__}"
            )
        result[str(doc_id)] = [str(a) for a in aliases]
    return result


def alias_text(aliases: list[str]) -> str:
    """Render aliases as one line indexed alongside the document (AC-20)."""
    return "Auch gesucht als: " + "; ".join(aliases)
