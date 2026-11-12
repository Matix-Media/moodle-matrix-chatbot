"""What tool cards show: typed display blocks, pinned to what local_llmchat renders.

The plugin (amd/src/tooldetails.js::describeResult, templates/tool_result.mustache)
renders each block by ``kind`` with exactly these field names; an unknown kind falls
back to a generic view. Changing a name here without the plugin breaks the cards.
"""

from __future__ import annotations

from typing import Any, ClassVar

from test_tools import NOW, ctx, world  # noqa: F401  (fixture re-export)

from moodle_llm_chat.agent.paths import Segment
from moodle_llm_chat.agent.tools import default_registry
from moodle_llm_chat.agent.tools.base import Tool, ToolResult, ToolUI
from moodle_llm_chat.agent.tools.display import (
    Column,
    EntriesBlock,
    Entry,
    FieldItem,
    FieldsBlock,
    HitItem,
    HitsBlock,
    NoticeBlock,
    TableBlock,
    TextBlock,
)
from moodle_llm_chat.agent.tools.files import ListDir, ListDocuments, ReadFile, StatFile
from moodle_llm_chat.agent.tools.info import CurrentDatetime, CurrentUser
from moodle_llm_chat.agent.tools.registry import ToolRegistry
from moodle_llm_chat.agent.tools.search import SearchRegex, SearchText
from moodle_llm_chat.index.store import Store


def kinds(result: ToolResult) -> list[str]:
    return [b.kind for b in result.display]


COURSE = Segment(kind="course", id="2", name="A")
MODULE = Segment(kind="module", id="11", name="B")
COURSE_JSON = {"kind": "course", "id": "2", "name": "A"}
MODULE_JSON = {"kind": "module", "id": "11", "name": "B"}


def ids(location: list[Segment]) -> list[str]:
    return [f"{s.kind}:{s.id}" for s in location]


def assert_no_paths(value: Any) -> None:
    """The plugin gets ids and names, never a path string to parse."""
    if isinstance(value, dict):
        assert "path" not in value, value
        for v in value.values():
            assert_no_paths(v)
    elif isinstance(value, list):
        for v in value:
            assert_no_paths(v)


class TestWire:
    def test_hits(self) -> None:
        block = HitsBlock(
            items=[HitItem(cite=1, location=[COURSE, MODULE], page=2, modified=5, snippet="x")]
        )
        assert block.model_dump(mode="json") == {
            "kind": "hits",
            "items": [
                {
                    "cite": 1,
                    "location": [COURSE_JSON, MODULE_JSON],
                    "page": 2,
                    "modified": 5,
                    "snippet": "x",
                }
            ],
        }

    def test_entries(self) -> None:
        block = EntriesBlock(
            location=[COURSE],
            items=[Entry(name="B", type="folder", location=[COURSE, MODULE], count=3, modified=5)],
        )
        assert block.model_dump(mode="json") == {
            "kind": "entries",
            "location": [COURSE_JSON],
            "items": [
                {
                    "name": "B",
                    "type": "folder",
                    "location": [COURSE_JSON, MODULE_JSON],
                    "detail": "",
                    "count": 3,
                    "pages": None,
                    "chars": None,
                    "modified": 5,
                    "cite": None,
                }
            ],
        }

    def test_fields(self) -> None:
        block = FieldsBlock(items=[FieldItem(key="modified", label="Geändert", value="2026-11-01")])
        assert block.model_dump(mode="json") == {
            "kind": "fields",
            "items": [
                {"key": "modified", "label": "Geändert", "value": "2026-11-01", "format": "text"}
            ],
        }

    def test_text(self) -> None:
        assert TextBlock(text="abc").model_dump(mode="json") == {
            "kind": "text",
            "text": "abc",
            "title": "",
            "location": [],
            "cite": None,
            "start": None,
            "end": None,
            "total": None,
        }

    def test_table(self) -> None:
        block = TableBlock(columns=[Column(key="course", label="Kurs")], rows=[["LF7"]])
        assert block.model_dump(mode="json") == {
            "kind": "table",
            "columns": [{"key": "course", "label": "Kurs"}],
            "rows": [["LF7"]],
        }

    def test_notice(self) -> None:
        assert NoticeBlock(level="info", code="no_hits", text="Nichts").model_dump(mode="json") == {
            "kind": "notice",
            "level": "info",
            "code": "no_hits",
            "text": "Nichts",
        }


class TestTools:
    async def test_search_shows_hits(self, world: Store) -> None:  # noqa: F811
        result = await SearchText().run(SearchText.Args(query="Raum"), ctx(world))
        (block,) = result.display
        assert isinstance(block, HitsBlock)
        (hit,) = block.items
        assert hit.cite == 1 and ids(hit.location) == ["course:2", "section:1", "module:11"]
        assert [s.name for s in hit.location] == ["LF7", "Woche 2", "Plan"]
        assert "B204" in hit.snippet and hit.modified

    async def test_search_without_hits_is_a_notice(self, world: Store) -> None:  # noqa: F811
        result = await SearchText().run(SearchText.Args(query="Zebrafink"), ctx(world))
        (block,) = result.display
        assert isinstance(block, NoticeBlock) and block.code == "no_hits"

    async def test_regex_shows_hits_with_page(self, world: Store) -> None:  # noqa: F811
        result = await SearchRegex().run(
            SearchRegex.Args(pattern=r"\d{2}\.\d{2}\.\d{4}"), ctx(world)
        )
        (block,) = result.display
        assert isinstance(block, HitsBlock)
        assert block.items[0].page == 1 and "24.11.2026" in block.items[0].snippet

    async def test_list_dir_shows_entries(self, world: Store) -> None:  # noqa: F811
        root = await ListDir().run(ListDir.Args(path="/"), ctx(world))
        (block,) = root.display
        assert isinstance(block, EntriesBlock) and block.location == []
        lf7 = next(e for e in block.items if e.name == "LF7")
        assert lf7.type == "folder" and ids(lf7.location) == ["course:2"]
        assert lf7.detail == "Netzwerke LF7"
        week = await ListDir().run(ListDir.Args(path="/LF7/Woche 1"), ctx(world))
        assert ids(week.display[0].location) == ["course:2", "section:1"]  # type: ignore[union-attr]
        (skript,) = week.display[0].items  # type: ignore[union-attr]
        assert skript.type == "inline" and skript.pages == 2
        assert ids(skript.location)[-1] == "module:10"

    async def test_stat_shows_fields(self, world: Store) -> None:  # noqa: F811
        result = await StatFile().run(StatFile.Args(path="/LF7/Woche 1/Skript"), ctx(world))
        (block,) = result.display
        assert isinstance(block, FieldsBlock)
        fields = {f.key: f for f in block.items}
        assert {"title", "kind", "size", "modified", "link"} <= set(fields)
        assert fields["modified"].format == "date" and fields["link"].format == "link"

    async def test_read_shows_the_excerpt(self, world: Store) -> None:  # noqa: F811
        c = ctx(world, read_max_chars=40)
        result = await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript"), c)
        (block,) = result.display
        assert isinstance(block, TextBlock)
        assert block.cite == 1 and block.title == "Skript"
        assert ids(block.location) == ["course:2", "section:1", "module:10"]
        assert block.start == 0 and block.end == 40 and (block.total or 0) > 40
        assert "[Seite 1]" in block.text

    async def test_list_documents_shows_entries_with_dates(self, world: Store) -> None:  # noqa: F811
        result = await ListDocuments().run(ListDocuments.Args(), ctx(world))
        (block,) = result.display
        assert isinstance(block, EntriesBlock)
        assert ids(block.items[0].location)[-1] == "module:11" and block.items[0].modified

    async def test_datetime_shows_fields(self, world: Store) -> None:  # noqa: F811
        result = await CurrentDatetime().run(CurrentDatetime.Args(), ctx(world))
        (block,) = result.display
        assert isinstance(block, FieldsBlock)
        fields = {f.key: f.value for f in block.items}
        assert fields["date"] == NOW.date().isoformat() and fields["week"] == "46"

    async def test_user_courses_are_a_table(self, world: Store) -> None:  # noqa: F811
        result = await CurrentUser().run(CurrentUser.Args(detail="courses"), ctx(world))
        assert kinds(result) == ["fields", "table"]
        table = result.display[1]
        assert isinstance(table, TableBlock)
        assert [c.key for c in table.columns] == ["course", "roles", "groups"]
        assert any(row[0].startswith("LF7") for row in table.rows)


class TestNoPathsOnTheWire:
    async def test_no_tool_result_carries_a_path(self, world: Store) -> None:  # noqa: F811
        c = ctx(world)
        results = [
            await SearchText().run(SearchText.Args(query="Raum"), c),
            await ListDir().run(ListDir.Args(path="/"), c),
            await ListDir().run(ListDir.Args(path="/LF7/Woche 1"), c),
            await StatFile().run(StatFile.Args(path="/LF7/Woche 1/Skript"), c),
            await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript"), c),
            await ListDocuments().run(ListDocuments.Args(), c),
        ]
        for result in results:
            assert_no_paths([b.model_dump(mode="json") for b in result.display])
            assert not any("/LF7" in str(b.model_dump(mode="json")) for b in result.display)
        assert_no_paths([r.model_dump(mode="json") for r in c.citations.refs])


class _Plain(Tool[Any]):
    name: ClassVar[str] = "plain"
    description: ClassVar[str] = "A future tool that only returns text and declares no display."
    ui: ClassVar[ToolUI] = ToolUI(label="Plain", icon="x", summary="")

    async def run(self, args: Any, ctx: Any) -> ToolResult:
        return ToolResult(text="Zeile eins\nZeile zwei " + "x" * 9000)


class TestBinding:
    async def test_text_only_tools_get_a_text_block(self, world: Store) -> None:  # noqa: F811
        registry = ToolRegistry()
        registry.register(_Plain())
        (bound,) = registry.build(ctx(world))
        message = await bound.ainvoke({"type": "tool_call", "name": "plain", "args": {}, "id": "c"})
        (block,) = message.artifact["display"]
        assert block["kind"] == "text" and block["text"].startswith("Zeile eins\nZeile zwei")
        assert len(block["text"]) < 5000

    async def test_errors_are_error_notices(self, world: Store) -> None:  # noqa: F811
        tools = {t.name: t for t in default_registry().build(ctx(world))}
        message = await tools["list_dir"].ainvoke(
            {"type": "tool_call", "name": "list_dir", "args": {"path": "/LF8"}, "id": "c"}
        )
        (block,) = message.artifact["display"]
        assert block["kind"] == "notice" and block["level"] == "error"
        assert block["code"] == "path_not_found" and "LF8" in block["text"]
