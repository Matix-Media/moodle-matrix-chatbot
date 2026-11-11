"""The folder-like view of a user's courses."""

from __future__ import annotations

import pytest
from factories import doc

from moodle_llm_chat.access.scope import AccessScope, Capability
from moodle_llm_chat.agent.paths import CourseTree, NodeKind, PathNotFound
from moodle_llm_chat.index.models import CourseIn
from moodle_llm_chat.index.store import Store

S = "site-a"


def scope(*cmids: int) -> AccessScope:
    return AccessScope(
        site_id=S,
        user_id=1,
        course_ids=frozenset({2, 3}),
        cmids=frozenset(cmids),
        caps=frozenset({Capability.READ}),
    )


@pytest.fixture
def tree_store(store: Store) -> Store:
    store.upsert_courses(
        S,
        [
            CourseIn(course_id=2, fullname="Netzwerke LF7", shortname="LF7"),
            CourseIn(course_id=3, fullname="Mathematik", shortname="MA"),
        ],
    )
    store.upsert_documents(
        S,
        [
            doc(
                "2:10:intro",
                cmid=10,
                section_name="Woche 1",
                section_order=1,
                module_name="Skript",
                title="Skript",
            ),
            doc(
                "2:11:0",
                cmid=11,
                section_name="Woche 1",
                section_order=1,
                module_name="Material",
                title="VLAN.pdf",
            ),
            doc(
                "2:11:1",
                cmid=11,
                section_name="Woche 1",
                section_order=1,
                module_name="Material",
                title="Routing.pdf",
            ),
            doc(
                "2:12:x",
                cmid=12,
                section_name="Woche 2",
                section_order=2,
                module_name="Quiz/Test",
                title="Quiz",
            ),
            doc(
                "3:20:y",
                cmid=20,
                section_name="",
                section_order=0,
                module_name="Formeln",
                title="Formeln",
            ),
            doc(
                "3:21:hidden",
                cmid=21,
                section_name="Geheim",
                section_order=5,
                module_name="Lösungen",
                title="Lösungen",
            ),
        ],
    )
    return store


def build(store: Store, *cmids: int) -> CourseTree:
    return CourseTree.build(store, scope(*cmids))


class TestStructure:
    def test_root_lists_courses_by_shortname(self, tree_store: Store) -> None:
        tree = build(tree_store, 10, 11, 12, 20)
        assert [n.name for n in tree.root.children.values()] == ["LF7", "MA"]

    def test_sections_in_order_with_fallback_name(self, tree_store: Store) -> None:
        tree = build(tree_store, 10, 11, 12, 20)
        assert list(tree.resolve("/LF7").children) == ["Woche 1", "Woche 2"]
        assert list(tree.resolve("/MA").children) == ["Abschnitt 0"]

    def test_single_document_module_is_a_file(self, tree_store: Store) -> None:
        node = build(tree_store, 10, 11).resolve("/LF7/Woche 1/Skript")
        assert node.kind is NodeKind.FILE
        assert node.document is not None and node.document.doc_id == "2:10:intro"

    def test_multi_document_module_is_a_directory(self, tree_store: Store) -> None:
        node = build(tree_store, 10, 11).resolve("/LF7/Woche 1/Material")
        assert node.kind is NodeKind.MODULE
        assert sorted(node.children) == ["Routing.pdf", "VLAN.pdf"]

    def test_slashes_in_names_are_replaced(self, tree_store: Store) -> None:
        tree = build(tree_store, 12)
        assert "Quiz-Test" in tree.resolve("/LF7/Woche 2").children

    def test_invisible_modules_and_their_sections_do_not_appear(self, tree_store: Store) -> None:
        tree = build(tree_store, 10, 20)
        assert "Geheim" not in tree.resolve("/MA").children
        with pytest.raises(PathNotFound):
            tree.resolve("/MA/Geheim/Lösungen")

    def test_path_for_document(self, tree_store: Store) -> None:
        tree = build(tree_store, 10, 11)
        assert tree.path_for("2:11:1") == "/LF7/Woche 1/Material/Routing.pdf"
        assert tree.path_for("3:21:hidden") is None


class TestResolution:
    def test_case_insensitive(self, tree_store: Store) -> None:
        assert build(tree_store, 10).resolve("/lf7/woche 1/skript").kind is NodeKind.FILE

    def test_unique_prefix(self, tree_store: Store) -> None:
        assert build(tree_store, 10, 11).resolve("/LF7/Woche 1/Mat").kind is NodeKind.MODULE

    def test_fuzzy_typo(self, tree_store: Store) -> None:
        assert build(tree_store, 10).resolve("/LF7/Woche 1/Skirpt").kind is NodeKind.FILE

    def test_course_by_fullname(self, tree_store: Store) -> None:
        assert build(tree_store, 20).resolve("/Mathematik").kind is NodeKind.COURSE

    def test_not_found_offers_suggestions(self, tree_store: Store) -> None:
        with pytest.raises(PathNotFound) as err:
            build(tree_store, 10, 11, 12).resolve("/LF7/Woche 9")
        assert "Woche 1" in err.value.suggestions

    def test_root_and_trailing_slash(self, tree_store: Store) -> None:
        tree = build(tree_store, 10)
        assert tree.resolve("/").kind is NodeKind.ROOT
        assert tree.resolve("").kind is NodeKind.ROOT
        assert tree.resolve("/LF7/").kind is NodeKind.COURSE

    def test_documents_under_a_node(self, tree_store: Store) -> None:
        tree = build(tree_store, 10, 11, 12)
        ids = {d.doc_id for d in tree.documents_under(tree.resolve("/LF7/Woche 1"))}
        assert ids == {"2:10:intro", "2:11:0", "2:11:1"}
