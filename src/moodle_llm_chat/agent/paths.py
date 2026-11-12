"""A folder-like view of the courses a user can see.

``/`` lists courses (by short name), ``/<course>`` its sections, a section its course
modules. A module with a single document *is* a file; a module with several (a
folder, a book) is a directory of them. Only modules in the user's scope exist in
the tree, so an invisible module — or a section with nothing visible — does not
appear at all.

Path segments resolve exactly, then case-insensitively, then by unique prefix, then
by close fuzzy match, so a model that slightly misspells a name still lands right.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import StrEnum

from moodle_llm_chat.access.scope import AccessScope
from moodle_llm_chat.index.models import Course, Document
from moodle_llm_chat.index.store import Store


class NodeKind(StrEnum):
    ROOT = "root"
    COURSE = "course"
    SECTION = "section"
    MODULE = "module"
    FILE = "file"


class PathNotFound(LookupError):
    def __init__(self, path: str, missing: str, suggestions: list[str]) -> None:
        super().__init__(f"{path!r}: {missing!r} not found")
        self.path = path
        self.missing = missing
        self.suggestions = suggestions


@dataclass
class Node:
    name: str
    kind: NodeKind
    path: str
    children: dict[str, Node] = field(default_factory=dict)
    document: Document | None = None
    course: Course | None = None
    aliases: tuple[str, ...] = ()

    @property
    def modified(self) -> int:
        if self.document is not None:
            return self.document.timemodified
        return max((c.modified for c in self.children.values()), default=0)


def _clean(name: str, fallback: str) -> str:
    cleaned = " ".join((name or "").replace("/", "-").split())
    return cleaned or fallback


def _unique(name: str, taken: dict[str, Node]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def _join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


class CourseTree:
    def __init__(self, root: Node, by_doc: dict[str, Node]) -> None:
        self.root = root
        self._by_doc = by_doc

    @classmethod
    def build(cls, store: Store, scope: AccessScope) -> CourseTree:
        root = Node(name="/", kind=NodeKind.ROOT, path="/")
        by_doc: dict[str, Node] = {}
        documents = store.live_documents(scope.site_id, cmids=scope.cmids)
        courses = {
            c.course_id: c for c in store.courses(scope.site_id, course_ids=scope.course_ids)
        }
        course_nodes: dict[int, Node] = {}

        for course_id in sorted({d.course_id for d in documents}):
            if course_id not in scope.course_ids:
                continue
            course = courses.get(course_id)
            name = _clean(course.shortname if course else "", f"Kurs {course_id}")
            name = _unique(name, root.children)
            node = Node(
                name=name,
                kind=NodeKind.COURSE,
                path=_join("/", name),
                course=course,
                aliases=(course.fullname,) if course else (),
            )
            root.children[name] = node
            course_nodes[course_id] = node

        grouped: dict[tuple[int, int], list[Document]] = {}
        for d in documents:
            if d.course_id in course_nodes:
                grouped.setdefault((d.course_id, d.cmid), []).append(d)

        # Keyed by id as well as order: until a resync lands, a newly inserted section can
        # share its order with the section it pushed down.
        section_nodes: dict[tuple[int, int, int], Node] = {}
        ordered = sorted(
            grouped.items(),
            key=lambda kv: (kv[1][0].section_order, kv[1][0].section_id, kv[1][0].doc_id),
        )
        for (course_id, _cmid), docs in ordered:
            course_node = course_nodes[course_id]
            first = docs[0]
            key = (course_id, first.section_id, first.section_order)
            section = section_nodes.get(key)
            if section is None:
                name = _unique(
                    _clean(first.section_name, f"Abschnitt {first.section_order}"),
                    course_node.children,
                )
                section = Node(name=name, kind=NodeKind.SECTION, path=_join(course_node.path, name))
                course_node.children[name] = section
                section_nodes[key] = section

            module_name = _unique(
                _clean(first.module_name or first.title, "Inhalt"), section.children
            )
            module_path = _join(section.path, module_name)
            if len(docs) == 1:
                file_node = Node(
                    name=module_name, kind=NodeKind.FILE, path=module_path, document=first
                )
                section.children[module_name] = file_node
                by_doc[first.doc_id] = file_node
                continue
            module = Node(name=module_name, kind=NodeKind.MODULE, path=module_path)
            section.children[module_name] = module
            for d in docs:
                name = _unique(_clean(d.title or d.filename or "", d.doc_id), module.children)
                file_node = Node(
                    name=name, kind=NodeKind.FILE, path=_join(module_path, name), document=d
                )
                module.children[name] = file_node
                by_doc[d.doc_id] = file_node
        return cls(root, by_doc)

    def path_for(self, doc_id: str) -> str | None:
        node = self._by_doc.get(doc_id)
        return node.path if node else None

    def node_for(self, doc_id: str) -> Node | None:
        return self._by_doc.get(doc_id)

    def resolve(self, path: str) -> Node:
        node = self.root
        for segment in [p for p in (path or "/").split("/") if p.strip()]:
            child = self._match(node, segment.strip())
            if child is None:
                close = difflib.get_close_matches(segment, list(node.children), n=3, cutoff=0.4)
                raise PathNotFound(path, segment, close or list(node.children)[:5])
            node = child
        return node

    @staticmethod
    def _match(node: Node, segment: str) -> Node | None:
        if segment in node.children:
            return node.children[segment]
        folded = segment.casefold()
        for child in node.children.values():
            if child.name.casefold() == folded or any(
                a.casefold() == folded for a in child.aliases
            ):
                return child
        prefixed = [c for c in node.children.values() if c.name.casefold().startswith(folded)]
        if len(prefixed) == 1:
            return prefixed[0]
        close = difflib.get_close_matches(
            folded, [c.casefold() for c in node.children], n=2, cutoff=0.75
        )
        if len(close) == 1:
            return next(c for c in node.children.values() if c.name.casefold() == close[0])
        return None

    def documents_under(self, node: Node) -> list[Document]:
        if node.document is not None:
            return [node.document]
        found: list[Document] = []
        for child in node.children.values():
            found.extend(self.documents_under(child))
        return found
