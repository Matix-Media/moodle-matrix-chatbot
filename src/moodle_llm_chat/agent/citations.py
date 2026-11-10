"""Stable ``[n]`` citation numbers for everything a run's tools showed the model."""

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
    page: int | None


class CitationRegistry:
    def __init__(self, existing: list[CitationRef] | None = None) -> None:
        self._refs: list[CitationRef] = list(existing or [])
        self._by_key = {(r.doc_id, r.page): r.index for r in self._refs}

    def add(
        self, document: Document, *, path: str, course_name: str, page: int | None = None
    ) -> int:
        key = (document.doc_id, page)
        if key in self._by_key:
            return self._by_key[key]
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
            )
        )
        self._by_key[key] = index
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
