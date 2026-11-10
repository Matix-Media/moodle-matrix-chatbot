"""Builders for test data (imported by test modules; fixtures live in conftest)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from moodle_llm_chat.index.models import DocumentIn, FileRef

EMBED_DIM = 3


def doc(doc_id: str = "2:10:intro", **overrides: Any) -> DocumentIn:
    """A minimal inline document in course 2."""
    course_id, cmid, *_ = [*doc_id.split(":"), "0", "0"][:3]
    base: dict[str, Any] = {
        "doc_id": doc_id,
        "course_id": int(course_id),
        "section_id": 1,
        "section_name": "Woche 1",
        "section_order": 1,
        "cmid": int(cmid),
        "modname": "page",
        "module_name": "Einführung",
        "title": "Einführung",
        "kind": "inline",
        "header_path": ["Netzwerke", "Woche 1", "Einführung"],
        "module_url": f"https://moodle.test/mod/page/view.php?id={cmid}",
        "timemodified": 1_700_000_000,
        "text": "Willkommen im Kurs Netzwerke.",
        "content_hash": f"h-{doc_id}",
    }
    base.update(overrides)
    return DocumentIn(**base)


def file_doc(doc_id: str, sha256: str, **overrides: Any) -> DocumentIn:
    return doc(
        doc_id,
        kind="file",
        text=None,
        file=FileRef(sha256=sha256, filename="skript.pdf", mimetype="application/pdf", filesize=10),
        **overrides,
    )


def read_disk(path: Path) -> bytes:
    data = b""
    for p in [path, Path(f"{path}-wal"), Path(f"{path}-shm"), *path.parent.rglob("*.enc")]:
        if p.exists():
            data += p.read_bytes()
    return data
