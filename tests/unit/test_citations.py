"""The citation registry: one stable number per document."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from moodle_llm_chat.agent.citations import CitationRef, CitationRegistry


def document(doc_id: str) -> Any:
    """Only the fields the registry reads."""
    return SimpleNamespace(doc_id=doc_id, title=doc_id, module_name="M", module_url=None)


def ref(index: int, doc_id: str, page: int | None) -> CitationRef:
    return CitationRef(
        index=index, doc_id=doc_id, title="T", course_name="C", path="/C/T", url=None, page=page
    )


class TestRegistry:
    def test_one_number_per_document_collecting_pages(self) -> None:
        reg = CitationRegistry()
        a = document("2:10:a")
        assert reg.add(a, path="/C/A", course_name="C", page=3) == 1
        assert reg.add(a, path="/C/A", course_name="C", page=1) == 1
        assert reg.add(a, path="/C/A", course_name="C") == 1
        assert reg.add(document("2:11:b"), path="/C/B", course_name="C", page=3) == 2
        first = reg.refs[0]
        assert first.pages == [1, 3] and first.page == 3

    def test_saved_per_page_refs_resolve_to_the_first_number(self) -> None:
        reg = CitationRegistry([ref(1, "2:10:a", 2), ref(2, "2:10:a", 5), ref(3, "2:11:b", None)])
        assert reg.refs[0].pages == [2]
        assert reg.add(document("2:10:a"), path="/C/A", course_name="C", page=7) == 1
        assert reg.refs[0].pages == [2, 7]
        assert reg.add(document("2:11:b"), path="/C/B", course_name="C") == 3
        assert [r.index for r in reg.cited_in("x [2] y [1, 3]")] == [2, 1, 3]
