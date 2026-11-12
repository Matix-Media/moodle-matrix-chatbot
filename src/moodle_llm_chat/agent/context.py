"""Everything a tool may touch during one turn. Tools reach state only through this."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from moodle_llm_chat.access.scope import AccessScope, UserProfile
from moodle_llm_chat.agent.citations import CitationRegistry
from moodle_llm_chat.agent.paths import CourseTree
from moodle_llm_chat.config import AgentSection
from moodle_llm_chat.index.models import Document, Site
from moodle_llm_chat.index.search import Searcher
from moodle_llm_chat.index.store import Store


class AccessChecker(Protocol):
    async def check(self, site: Site, user_id: int, cmids: Iterable[int]) -> set[int]: ...

    async def user_info(self, site: Site, user_id: int) -> dict[str, Any]: ...


@dataclass
class RunContext:
    site: Site
    scope: AccessScope
    user: UserProfile
    store: Store
    searcher: Searcher
    access: AccessChecker
    settings: AgentSection
    clock: Callable[[], datetime]
    citations: CitationRegistry = field(default_factory=CitationRegistry)
    emit: Callable[[dict[str, Any]], None] | None = None
    _tree: CourseTree | None = None

    @property
    def tree(self) -> CourseTree:
        """The user's course tree, built once per turn."""
        if self._tree is None:
            self._tree = CourseTree.build(self.store, self.scope)
        return self._tree

    def now(self) -> datetime:
        return self.clock()

    async def readable(self, documents: Iterable[Document]) -> list[Document]:
        """Filter to documents Moodle confirms, right now, that the user may read.

        Raises :class:`~moodle_llm_chat.access.moodle.MoodleUnavailable` when Moodle
        cannot be asked — callers must then show nothing (fail closed).
        """
        docs = [d for d in documents if d.cmid in self.scope.cmids]
        if not docs:
            return []
        allowed = await self.access.check(self.site, self.scope.user_id, {d.cmid for d in docs})
        return [d for d in docs if d.cmid in allowed]

    def cite(self, document: Document, *, page: int | None = None) -> int:
        node = self.tree.node_for(document.doc_id)
        course = self.tree.course_of(node) if node else None
        return self.citations.add(
            document,
            location=self.tree.location(node) if node else [],
            course_name=course.fullname if course else "",
            page=page,
        )
