"""Built-in tools, each run directly against a real store and a fake Moodle."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from factories import doc

from moodle_llm_chat.access.moodle import MoodleUnavailable
from moodle_llm_chat.access.scope import AccessScope, Capability, UserProfile
from moodle_llm_chat.agent.context import RunContext
from moodle_llm_chat.agent.paths import PathNotFound
from moodle_llm_chat.agent.tools.files import ListDir, ListDocuments, ReadFile, StatFile
from moodle_llm_chat.agent.tools.info import CurrentDatetime, CurrentUser
from moodle_llm_chat.agent.tools.registry import ToolInputError
from moodle_llm_chat.agent.tools.search import SearchContent, SearchRegex, SearchText
from moodle_llm_chat.config import AgentSection
from moodle_llm_chat.index.models import ChunkIn, CourseIn, SegmentIn, Site
from moodle_llm_chat.index.search import Searcher
from moodle_llm_chat.index.store import Store

S = "site-a"
TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 10, 9, 30, tzinfo=TZ)
DAY = 86400
T0 = int(NOW.timestamp())


class FakeMoodle:
    def __init__(self, *, deny: Iterable[int] = (), down: bool = False) -> None:
        self.deny = set(deny)
        self.down = down
        self.calls: list[set[int]] = []

    async def check(self, site: Site, user_id: int, cmids: Iterable[int]) -> set[int]:
        if self.down:
            raise MoodleUnavailable("down")
        wanted = set(cmids)
        self.calls.append(wanted)
        return wanted - self.deny

    async def user_info(self, site: Site, user_id: int) -> dict[str, Any]:
        return {"groups": {2: ["Gruppe B"]}}


def index(store: Store, doc_id: str, cmid: int, pages: list[str], **kw: Any) -> None:
    store.upsert_documents(S, [doc(doc_id, cmid=cmid, **kw)])
    segs = [
        SegmentIn(ordinal=i, text=t, page=i + 1 if len(pages) > 1 else None)
        for i, t in enumerate(pages)
    ]
    chunks = [
        ChunkIn(ordinal=i, text=t, header_text="", page=s.page)
        for i, (t, s) in enumerate(zip(pages, segs, strict=True))
    ]
    store.replace_content(S, doc_id, segs, chunks)
    store.record_extraction(S, doc_id, extract_version=1)


@pytest.fixture
def world(store: Store) -> Store:
    store.add_site(S, name="A", url="https://moodle.a.test")
    store.upsert_courses(
        S,
        [
            CourseIn(course_id=2, fullname="Netzwerke LF7", shortname="LF7"),
            CourseIn(course_id=3, fullname="Mathematik", shortname="MA"),
        ],
    )
    index(
        store,
        "2:10:skript",
        10,
        [
            "Seite eins: Ein VLAN trennt Netze. Prüfung am 24.11.2026.",
            "Seite zwei: Trunking und Tagging.",
        ],
        section_name="Woche 1",
        section_order=1,
        module_name="Skript",
        title="Skript",
        timemodified=T0 - 10 * DAY,
    )
    index(
        store,
        "2:11:plan",
        11,
        ["Stundenplan: Raum B204 am Montag."],
        section_name="Woche 2",
        section_order=2,
        module_name="Plan",
        title="Plan",
        timemodified=T0 - 1 * DAY,
    )
    index(
        store,
        "3:20:formeln",
        20,
        ["Satz des Pythagoras."],
        section_name="Grundlagen",
        section_order=1,
        module_name="Formeln",
        title="Formeln",
        timemodified=T0 - 3 * DAY,
    )
    index(
        store,
        "2:12:loesung",
        12,
        ["Geheime Musterlösung zum VLAN Test."],
        section_name="Woche 1",
        section_order=1,
        module_name="Lösung",
        title="Lösung",
        timemodified=T0,
    )
    return store


def ctx(
    store: Store,
    *,
    cmids: Iterable[int] = (10, 11, 20),
    moodle: FakeMoodle | None = None,
    **kw: Any,
) -> RunContext:
    scope = AccessScope(
        site_id=S,
        user_id=7,
        course_ids=frozenset({2, 3}),
        cmids=frozenset(cmids),
        caps=frozenset({Capability.READ, Capability.INTERACT}),
    )
    return RunContext(
        site=store.site(S),
        scope=scope,
        user=UserProfile(
            user_id=7,
            fullname="Erika",
            roles={2: ["student"], 3: ["editingteacher"]},
            course_context=2,
        ),
        store=store,
        searcher=Searcher(store, None),
        access=moodle or FakeMoodle(),
        settings=AgentSection(read_max_chars=kw.pop("read_max_chars", 6000)),
        clock=lambda: NOW,
    )


class TestSearch:
    async def test_content_search_cites_and_hides_out_of_scope(self, world: Store) -> None:
        c = ctx(world)
        result = await SearchContent().run(SearchContent.Args(query="VLAN"), c)
        assert "[1] /LF7/Woche 1/Skript" in result.text
        assert "Musterlösung" not in result.text
        assert result.citations == [1]

    async def test_live_denial_hides_hits(self, world: Store) -> None:
        result = await SearchContent().run(
            SearchContent.Args(query="VLAN"), ctx(world, moodle=FakeMoodle(deny={10}))
        )
        assert "Keine Treffer" in result.text

    async def test_moodle_down_fails_closed(self, world: Store) -> None:
        with pytest.raises(MoodleUnavailable):
            await SearchContent().run(
                SearchContent.Args(query="VLAN"), ctx(world, moodle=FakeMoodle(down=True))
            )

    async def test_path_restriction(self, world: Store) -> None:
        result = await SearchText().run(
            SearchText.Args(query="Raum VLAN", path="/LF7/Woche 2"), ctx(world)
        )
        assert "Woche 2/Plan" in result.text and "Skript" not in result.text

    async def test_since_filter(self, world: Store) -> None:
        result = await SearchText().run(SearchText.Args(query="VLAN Raum", since="-2d"), ctx(world))
        assert "Plan" in result.text and "Skript" not in result.text

    async def test_citation_numbers_are_stable_across_calls(self, world: Store) -> None:
        c = ctx(world)
        await SearchText().run(SearchText.Args(query="Raum"), c)
        again = await SearchText().run(SearchText.Args(query="Raum"), c)
        assert again.citations == [1]

    async def test_hit_header_names_change_date_without_stand(self, world: Store) -> None:
        result = await SearchText().run(SearchText.Args(query="Raum"), ctx(world))
        assert "Stand" not in result.text
        assert "[1] /LF7/Woche 2/Plan · geändert 09.11.2026" in result.text

    async def test_pages_of_one_document_share_one_citation(self, world: Store) -> None:
        c = ctx(world)
        result = await SearchText().run(SearchText.Args(query="Seite"), c)
        assert result.citations == [1]
        assert "[1] /LF7/Woche 1/Skript, S. 1" in result.text
        assert "[1] /LF7/Woche 1/Skript, S. 2" in result.text
        (ref,) = c.citations.refs
        assert ref.pages == [1, 2]

    async def test_read_and_stat_reuse_the_search_citation(self, world: Store) -> None:
        c = ctx(world)
        await SearchText().run(SearchText.Args(query="Trunking"), c)
        read = await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript"), c)
        stat = await StatFile().run(StatFile.Args(path="/LF7/Woche 1/Skript"), c)
        assert read.citations == stat.citations == [1]
        assert len(c.citations.refs) == 1


class TestRegex:
    async def test_finds_dates_with_context_and_page(self, world: Store) -> None:
        result = await SearchRegex().run(
            SearchRegex.Args(pattern=r"\d{2}\.\d{2}\.\d{4}"), ctx(world)
        )
        assert "24.11.2026" in result.text
        assert "S. 1" in result.text

    async def test_out_of_scope_text_is_never_scanned(self, world: Store) -> None:
        result = await SearchRegex().run(SearchRegex.Args(pattern="Musterlösung"), ctx(world))
        assert "Keine Treffer" in result.text

    @pytest.mark.parametrize("pattern", ["(a+)+$", "(.*)*x", "x" * 300, "([unclosed"])
    async def test_dangerous_or_invalid_patterns_are_rejected(
        self, world: Store, pattern: str
    ) -> None:
        with pytest.raises(ToolInputError):
            await SearchRegex().run(SearchRegex.Args(pattern=pattern), ctx(world))

    async def test_match_limit(self, world: Store) -> None:
        result = await SearchRegex().run(SearchRegex.Args(pattern="e", max_matches=2), ctx(world))
        assert result.meta["matches"] == 2 and result.meta["limit_reached"]


class TestFiles:
    async def test_root_lists_courses(self, world: Store) -> None:
        result = await ListDir().run(ListDir.Args(path="/"), ctx(world))
        assert "LF7/" in result.text and "MA/" in result.text
        assert "Netzwerke LF7" in result.text

    async def test_section_listing_hides_invisible_module(self, world: Store) -> None:
        result = await ListDir().run(ListDir.Args(path="/LF7/Woche 1"), ctx(world))
        assert "Skript" in result.text
        assert "Lösung" not in result.text

    async def test_live_denied_file_is_not_listed(self, world: Store) -> None:
        result = await ListDir().run(
            ListDir.Args(path="/LF7/Woche 1"), ctx(world, moodle=FakeMoodle(deny={10}))
        )
        assert "Skript" not in result.text

    async def test_list_dir_on_file_is_an_input_error(self, world: Store) -> None:
        with pytest.raises(ToolInputError):
            await ListDir().run(ListDir.Args(path="/LF7/Woche 1/Skript"), ctx(world))

    async def test_unknown_path_suggests(self, world: Store) -> None:
        with pytest.raises(PathNotFound):
            await ListDir().run(ListDir.Args(path="/LF8"), ctx(world))

    async def test_stat(self, world: Store) -> None:
        result = await StatFile().run(StatFile.Args(path="/LF7/Woche 1/Skript"), ctx(world))
        assert "2 Seiten" in result.text
        assert "https://moodle.test/mod/page/view.php?id=10" in result.text

    async def test_read_whole_file_with_page_markers(self, world: Store) -> None:
        result = await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript"), ctx(world))
        assert "[Seite 1]" in result.text and "[Seite 2]" in result.text
        assert result.next_cursor is None

    async def test_partial_read_and_continue(self, world: Store) -> None:
        c = ctx(world, read_max_chars=40)
        first = await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript"), c)
        assert first.truncated and first.next_cursor == "40"
        assert "Trunking" not in first.text
        assert first.meta["total_chars"] > 40
        rest = await ReadFile().run(
            ReadFile.Args(path="/LF7/Woche 1/Skript", offset=40, limit=10_000), c
        )
        assert "Zeichen 40–" in rest.text
        assert "Trunking" in rest.text
        assert rest.next_cursor is None

    async def test_read_single_page(self, world: Store) -> None:
        result = await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript", page=2), ctx(world))
        assert "Trunking" in result.text and "Seite eins" not in result.text

    async def test_read_missing_page(self, world: Store) -> None:
        with pytest.raises(ToolInputError):
            await ReadFile().run(ReadFile.Args(path="/LF7/Woche 1/Skript", page=9), ctx(world))

    async def test_read_denied_live(self, world: Store) -> None:
        result = await ReadFile().run(
            ReadFile.Args(path="/LF7/Woche 1/Skript"), ctx(world, moodle=FakeMoodle(deny={10}))
        )
        assert "keinen Zugriff" in result.text
        assert "VLAN" not in result.text

    async def test_list_documents_newest_first_and_since(self, world: Store) -> None:
        result = await ListDocuments().run(ListDocuments.Args(), ctx(world))
        lines = result.text.splitlines()
        assert lines[0].endswith("/LF7/Woche 2/Plan")
        assert "Lösung" not in result.text
        recent = await ListDocuments().run(ListDocuments.Args(since="-5d"), ctx(world))
        assert "Skript" not in recent.text and "Formeln" in recent.text


class TestInfo:
    async def test_datetime(self, world: Store) -> None:
        result = await CurrentDatetime().run(CurrentDatetime.Args(), ctx(world))
        assert "Dienstag, 10.11.2026, 09:30" in result.text
        assert "Kalenderwoche 46" in result.text

    async def test_user_summary(self, world: Store) -> None:
        result = await CurrentUser().run(CurrentUser.Args(), ctx(world))
        assert "Erika" in result.text
        assert "Aktueller Kurs: Netzwerke LF7" in result.text
        assert "Lehrkraft: 1" in result.text

    async def test_user_courses_with_roles_and_groups(self, world: Store) -> None:
        result = await CurrentUser().run(CurrentUser.Args(detail="courses"), ctx(world))
        assert "Teilnehmer/in" in result.text and "Trainer/in" in result.text
        assert "Gruppe B" in result.text

    async def test_user_info_never_contains_contact_data(self, world: Store) -> None:
        result = await CurrentUser().run(CurrentUser.Args(detail="access"), ctx(world))
        assert "@" not in result.text
