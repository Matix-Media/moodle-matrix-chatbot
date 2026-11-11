"""Stable ``[n]`` citation numbers for everything a run's tools showed the model.

One number per document: every page of a document links to the same Moodle module, so
numbering pages separately only listed the same source several times. The pages the
tools showed are collected on the ref instead.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from moodle_llm_chat.index.models import Document

_MARKER_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


class CitationRef(BaseModel):
    index: int
    doc_id: str
    title: str
    course_name: str
    path: str
    url: str | None
    #: First page the tools showed; kept for conversations saved before ``pages``.
    page: int | None
    #: Every page of the document the tools showed, ascending.
    pages: list[int] = []


class CitationRegistry:
    def __init__(self, existing: list[CitationRef] | None = None) -> None:
        self._refs: list[CitationRef] = []
        self._by_doc: dict[str, int] = {}
        for ref in existing or []:
            if not ref.pages and ref.page is not None:
                ref = ref.model_copy(update={"pages": [ref.page]})
            self._refs.append(ref)
            self._by_doc.setdefault(ref.doc_id, ref.index)

    def add(
        self, document: Document, *, path: str, course_name: str, page: int | None = None
    ) -> int:
        known = self._by_doc.get(document.doc_id)
        if known is not None:
            ref = self._refs[known - 1]
            if page is not None and page not in ref.pages:
                ref.pages = sorted([*ref.pages, page])
                if ref.page is None:
                    ref.page = page
            return known
        index = len(self._refs) + 1
        self._refs.append(
            CitationRef(
                index=index,
                doc_id=document.doc_id,
                title=document.title or document.module_name,
                course_name=course_name,
                path=path,
                url=document.module_url,
                page=page,
                pages=[page] if page is not None else [],
            )
        )
        self._by_doc[document.doc_id] = index
        return index

    @property
    def refs(self) -> list[CitationRef]:
        return list(self._refs)

    def get(self, index: int) -> CitationRef | None:
        return self._refs[index - 1] if 0 < index <= len(self._refs) else None

    def cited_in(self, text: str) -> list[CitationRef]:
        """The citations an answer actually references, in order of first use."""
        seen: list[int] = []
        for match in _MARKER_RE.finditer(text):
            for part in match.group(1).split(","):
                n = int(part)
                if n not in seen and self.get(n) is not None:
                    seen.append(n)
        return [ref for n in seen if (ref := self.get(n)) is not None]
