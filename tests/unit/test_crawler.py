"""Verifies spec 003 — course crawl and content model.

The fixture mirrors shapes observed on the live site, including the traps:
``page``/``book`` HTML reported as ``filesize: 0``, a ``subsection`` with no
contents, and a module hidden from the student.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bsbot.ingest.crawler import CourseCrawler, diff_crawls
from bsbot.ingest.model import ContentItem, ContentKind

FIXTURES = Path(__file__).parent.parent / "fixtures" / "moodle"
COURSE = {"id": 42, "fullname": "Lernfeld 05 IT", "shortname": "LF05IT", "timemodified": 1700000000}


class FakeMoodle:
    """Stands in for MoodleClient; records what was asked for."""

    def __init__(self, contents: Any, courses: list[dict[str, Any]] | None = None) -> None:
        self._contents = contents
        self._courses = courses if courses is not None else [COURSE]
        self.calls: list[str] = []

    async def site_info(self) -> Any:
        class Info:
            userid = 4711

        return Info()

    async def call(self, wsfunction: str, **params: Any) -> Any:
        self.calls.append(wsfunction)
        if wsfunction == "core_enrol_get_users_courses":
            return self._courses
        if wsfunction == "core_course_get_contents":
            result = self._contents
            if isinstance(result, Exception):
                raise result
            if callable(result):
                return result(params["courseid"])
            return result
        raise AssertionError(f"unexpected call {wsfunction}")


@pytest.fixture
def contents() -> Any:
    return json.loads((FIXTURES / "course_contents.json").read_text())


async def crawl(contents: Any, **kwargs: Any) -> list[ContentItem]:
    crawler = CourseCrawler(FakeMoodle(contents), **kwargs)  # type: ignore[arg-type]
    return (await crawler.crawl()).items


def by_id(items: list[ContentItem], doc_id: str) -> ContentItem:
    matches = [i for i in items if i.doc_id == doc_id]
    assert matches, f"{doc_id} not found in {[i.doc_id for i in items]}"
    return matches[0]


class TestDiscovery:
    async def test_walks_every_course(self, contents: Any) -> None:
        """AC-1"""
        crawler = CourseCrawler(FakeMoodle(contents, courses=[COURSE, {**COURSE, "id": 43}]))  # type: ignore[arg-type]
        result = await crawler.crawl()
        assert result.courses_ok == 2

    async def test_one_failing_course_does_not_abort_the_rest(self, contents: Any) -> None:
        """AC-1: a single broken course must not lose the whole sync."""

        def per_course(courseid: int) -> Any:
            if courseid == 43:
                raise RuntimeError("boom")
            return contents

        crawler = CourseCrawler(FakeMoodle(per_course, courses=[COURSE, {**COURSE, "id": 43}]))  # type: ignore[arg-type]
        result = await crawler.crawl()
        assert result.courses_ok == 1
        assert result.courses_failed == 1
        assert result.items, "surviving course must still yield items"

    async def test_doc_ids_are_stable_and_unique(self, contents: Any) -> None:
        """AC-2: re-crawling must update rows, not duplicate them."""
        first = await crawl(contents)
        second = await crawl(contents)
        assert [i.doc_id for i in first] == [i.doc_id for i in second]
        assert len({i.doc_id for i in first}) == len(first)

    async def test_header_path_describes_location(self, contents: Any) -> None:
        """AC-3"""
        item = by_id(await crawl(contents), "42:5003:0")
        assert item.header_path == ["Lernfeld 05 IT", "Allgemeines", "Skript"]
        assert item.header_text == "Lernfeld 05 IT › Allgemeines › Skript"

    async def test_module_view_url_is_kept_for_citation(self, contents: Any) -> None:
        """AC-4"""
        assert by_id(await crawl(contents), "42:5003:0").module_url == (
            "https://moodle.example.de/mod/resource/view.php?id=5003"
        )


class TestTextSources:
    async def test_label_description_becomes_inline_text(self, contents: Any) -> None:
        """AC-5: labels hold real content and must be indexed, HTML stripped."""
        item = by_id(await crawl(contents), "42:5001:0")
        assert item.kind is ContentKind.INLINE
        assert item.text is not None
        assert "Die Klausur ist am 15.03.2026." in item.text
        assert "<b>" not in item.text
        assert "LF05" in item.text

    async def test_module_intro_description_yields_its_own_item(self, contents: Any) -> None:
        """AC-6: a page's intro text is content in its own right."""
        item = by_id(await crawl(contents), "42:5002:intro")
        assert item.kind is ContentKind.INLINE
        assert item.text == "Der Kursplan als Übersicht."

    async def test_page_yields_a_fetchable_html_item(self, contents: Any) -> None:
        """AC-7"""
        item = by_id(await crawl(contents), "42:5002:0")
        assert item.kind is ContentKind.HTML
        assert item.file is not None
        assert item.file.url.endswith("index.html?forcedownload=1")
        assert item.extractable is True

    async def test_book_yields_one_item_per_chapter_with_titles(self, contents: Any) -> None:
        """AC-8: cite the chapter, not the whole book."""
        items = await crawl(contents)
        chapters = [i for i in items if i.modname == "book" and i.kind is ContentKind.HTML]
        assert len(chapters) == 2
        assert [c.title for c in chapters] == ["Einleitung", "Netzwerke"]
        assert chapters[0].header_path[-1] == "Einleitung"
        assert "/15535/" in chapters[0].file.url  # type: ignore[union-attr]

    async def test_folder_yields_one_item_per_file(self, contents: Any) -> None:
        """AC-9"""
        items = [i for i in await crawl(contents) if i.module_id == 5005 and i.file]
        assert {i.file.filename for i in items if i.file} == {"hausordnung.pdf", "riesig.zip"}

    async def test_url_module_yields_external_link(self, contents: Any) -> None:
        """AC-10: and the HTML entity in the URL must be decoded."""
        item = by_id(await crawl(contents), "42:5006:0")
        assert item.kind is ContentKind.EXTERNAL
        assert item.file is None
        assert item.external_url is not None
        assert item.external_url.startswith("https://cloud.example.de/index.php/s/37rmq3xrWBwxra3")
        assert "&amp;" not in item.external_url

    async def test_subsection_produces_nothing_and_does_not_crash(self, contents: Any) -> None:
        """AC-11: Moodle 4.5+ container module."""
        assert not [i for i in await crawl(contents) if i.module_id == 5007]


class TestFiltering:
    async def test_hidden_modules_are_skipped(self, contents: Any) -> None:
        """AC-12: never surface what the student cannot see."""
        assert not [i for i in await crawl(contents) if i.module_id == 5008]

    async def test_video_media_is_recorded_but_not_extractable(self, contents: Any) -> None:
        """AC-13: video/audio have no OCR-style fallback, unlike images."""
        video = next(
            i for i in await crawl(contents) if i.file and i.file.filename.endswith(".mp4")
        )
        assert video.extractable is False
        assert video.skip_reason == "binary-type"

    async def test_image_attachments_are_now_extractable(self, contents: Any) -> None:
        """Spec 006 AC-22: images get the same OCR-style fallback as scanned PDFs,
        so they must no longer be excluded before extraction is even considered."""
        png = next(i for i in await crawl(contents) if i.file and i.file.filename.endswith(".png"))
        assert png.extractable is True
        assert png.skip_reason is None

    async def test_oversized_files_are_excluded(self, contents: Any) -> None:
        """AC-14: three files account for 123 MB of the live corpus."""
        big = next(i for i in await crawl(contents) if i.file and i.file.filename == "riesig.zip")
        assert big.extractable is False

    async def test_size_cap_does_not_drop_zero_byte_html(self, contents: Any) -> None:
        """AC-14, the important half: Moodle reports generated HTML as 0 bytes.

        A naive ``0 < size <= cap`` filter silently deletes 110 pages and 45 book
        chapters — most of the site's prose.
        """
        items = await crawl(contents, max_file_bytes=1)
        html = [i for i in items if i.kind is ContentKind.HTML]
        assert len(html) == 3  # 1 page + 2 book chapters
        assert all(i.extractable for i in html)

    async def test_gradebook_functions_are_never_called(self, contents: Any) -> None:
        """AC-15"""
        fake = FakeMoodle(contents)
        await CourseCrawler(fake).crawl()  # type: ignore[arg-type]
        assert not [c for c in fake.calls if "grade" in c or "submission" in c]


class TestChangeDetection:
    async def test_timemodified_prefers_contentsinfo(self, contents: Any) -> None:
        """AC-16: cheap freshness signal, no download required."""
        assert by_id(await crawl(contents), "42:5002:0").timemodified == 1787296810

    async def test_diff_reports_added_changed_and_removed(self, contents: Any) -> None:
        """AC-17: removed content must be tombstoned, not left answerable."""
        before = await crawl(contents)
        after = [i for i in before if i.doc_id != "42:5001:0"]
        after = [
            i.model_copy(update={"timemodified": i.timemodified + 1})
            if i.doc_id == "42:5003:0"
            else i
            for i in after
        ]
        after.append(before[0].model_copy(update={"doc_id": "42:9999:0"}))

        delta = diff_crawls(before, after)
        assert delta.removed == {"42:5001:0"}
        assert delta.changed == {"42:5003:0"}
        assert delta.added == {"42:9999:0"}


class TestTitleHygiene:
    """Moodle derives label module names from their content, so they arrive with
    embedded newlines and can run to hundreds of characters. Left alone they
    corrupt both the citation label and the embedding context prefix."""

    async def test_titles_are_collapsed_to_one_line(self) -> None:
        contents = [
            {
                "id": 1,
                "name": "Allgemeines\n",
                "visible": 1,
                "modules": [
                    {
                        "id": 7001,
                        "name": "\nLiebe Schüler*innen,\n\nwir begrüßen Sie   herzlich",
                        "modname": "label",
                        "visible": 1,
                        "uservisible": True,
                        "description": "<p>Hallo</p>",
                    }
                ],
            }
        ]
        item = (await crawl(contents))[0]
        assert "\n" not in item.title
        assert "  " not in item.title
        assert item.title == "Liebe Schüler*innen, wir begrüßen Sie herzlich"
        assert all("\n" not in part for part in item.header_path)

    async def test_overlong_titles_are_truncated(self) -> None:
        contents = [
            {
                "id": 1,
                "name": "Sec",
                "visible": 1,
                "modules": [
                    {
                        "id": 7002,
                        "name": "A" * 300,
                        "modname": "label",
                        "visible": 1,
                        "uservisible": True,
                        "description": "<p>x</p>",
                    }
                ],
            }
        ]
        item = (await crawl(contents))[0]
        assert len(item.title) <= 80
        assert item.title.endswith("…")


class LinkedCourseFakeMoodle:
    """Supports the enrolment call sequence: enrol methods -> instance info ->
    enrol_user -> re-query core_enrol_get_users_courses for the new course's name.
    """

    def __init__(
        self,
        contents_by_course: dict[int, Any],
        courses: list[dict[str, Any]],
        *,
        enrolment_methods: dict[int, list[dict[str, Any]]] | None = None,
        instance_info: dict[int, dict[str, Any]] | None = None,
        enrol_result: dict[str, Any] | None = None,
        courses_after_enrol: list[dict[str, Any]] | None = None,
        modules: dict[int, int] | None = None,
    ) -> None:
        self._contents_by_course = contents_by_course
        self._courses = courses
        self._enrolment_methods = enrolment_methods or {}
        self._instance_info = instance_info or {}
        self._enrol_result = enrol_result or {"status": True, "warnings": []}
        self._courses_after_enrol = courses_after_enrol
        #: cmid -> owning course id, for core_course_get_course_module.
        self._modules = modules or {}
        self._enrolled = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def site_info(self) -> Any:
        class Info:
            userid = 4711

        return Info()

    async def call(self, wsfunction: str, **params: Any) -> Any:
        self.calls.append((wsfunction, params))
        if wsfunction == "core_enrol_get_users_courses":
            if self._enrolled and self._courses_after_enrol is not None:
                return self._courses_after_enrol
            return self._courses
        if wsfunction == "core_course_get_contents":
            return self._contents_by_course[params["courseid"]]
        if wsfunction == "core_enrol_get_course_enrolment_methods":
            return self._enrolment_methods.get(params["courseid"], [])
        if wsfunction == "enrol_self_get_instance_info":
            return self._instance_info.get(params["instanceid"], {})
        if wsfunction == "enrol_self_enrol_user":
            if self._enrol_result.get("status"):
                self._enrolled = True
            return self._enrol_result
        if wsfunction == "core_course_get_course_module":
            cmid = params["cmid"]
            if cmid not in self._modules:
                raise RuntimeError("module not found")
            return {"cm": {"id": cmid, "course": self._modules[cmid]}, "warnings": []}
        raise AssertionError(f"unexpected call {wsfunction}")


def _course_with_link(course_id: int, section_name: str, link_url: str) -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "name": section_name,
            "visible": 1,
            "modules": [
                {
                    "id": 5001,
                    "name": "Anderer Kurs",
                    "modname": "url",
                    "instance": 900,
                    "visible": 1,
                    "uservisible": True,
                    "contextid": 260001,
                    "url": "https://moodle.itech-bs14.de/mod/url/view.php?id=5001",
                    "contents": [
                        {
                            "type": "url",
                            "filename": "Anderer Kurs",
                            "filepath": None,
                            "filesize": 0,
                            "fileurl": link_url,
                            "timemodified": 1700000000,
                            "sortorder": None,
                        }
                    ],
                }
            ],
        }
    ]


class TestLinkedCourseDiscovery:
    """Spec 010: linked courses are followed and self-enrolled when key-free."""

    async def test_follows_and_crawls_a_key_free_linked_course(self) -> None:
        """AC-9"""
        source = {**COURSE, "id": 100}
        linked = {
            "id": 934,
            "fullname": "Wirtschaftliche Betrachtung",
            "shortname": "WB",
            "timemodified": 1700000000,
        }
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100, "Lernfeld 10", "https://moodle.itech-bs14.de/course/view.php?id=934"
                ),
                934: [
                    {
                        "id": 2,
                        "name": "Start",
                        "visible": 1,
                        "modules": [
                            {
                                "id": 6001,
                                "name": "Info",
                                "modname": "label",
                                "visible": 1,
                                "uservisible": True,
                                "description": "<p>Wirtschaftliche Inhalte.</p>",
                            },
                        ],
                    }
                ],
            },
            courses=[source],
            enrolment_methods={
                934: [{"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True}]
            },
            instance_info={
                3012: {"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True}
            },
            courses_after_enrol=[source, linked],
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        result = await crawler.crawl()

        assert result.courses_ok == 2
        assert any(i.course_id == 934 for i in result.items)
        assert any("Wirtschaftliche Inhalte" in (i.text or "") for i in result.items)
        assert (
            "enrol_self_enrol_user",
            {"courseid": 934, "instanceid": 3012, "password": ""},
        ) in moodle.calls

    async def test_disabled_by_default(self) -> None:
        """AC-1: opt-in behaviour, off unless explicitly enabled."""
        source = {**COURSE, "id": 100}
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100, "Lernfeld 10", "https://moodle.itech-bs14.de/course/view.php?id=934"
                ),
            },
            courses=[source],
        )
        crawler = CourseCrawler(moodle)  # type: ignore[arg-type]
        result = await crawler.crawl()

        assert result.courses_ok == 1
        assert not any(c[0] == "core_enrol_get_course_enrolment_methods" for c in moodle.calls)

    async def test_already_enrolled_course_is_not_reattempted(self) -> None:
        """AC-2"""
        source = {**COURSE, "id": 100}
        other = {**COURSE, "id": 934, "fullname": "Bereits eingeschrieben"}
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100, "Lernfeld 10", "https://moodle.itech-bs14.de/course/view.php?id=934"
                ),
                934: [],
            },
            courses=[source, other],
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        await crawler.crawl()
        assert not any(c[0] == "core_enrol_get_course_enrolment_methods" for c in moodle.calls)

    async def test_key_required_course_is_not_joined(self) -> None:
        """AC-6: the safety property, exercised through the crawler too."""
        source = {**COURSE, "id": 100}
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100, "Lernfeld 10", "https://moodle.itech-bs14.de/course/view.php?id=934"
                ),
            },
            courses=[source],
            enrolment_methods={
                934: [{"id": 3012, "courseid": 934, "type": "self", "name": "Self", "status": True}]
            },
            instance_info={
                3012: {
                    "id": 3012,
                    "courseid": 934,
                    "type": "self",
                    "name": "Self",
                    "status": True,
                    "enrolpassword": "geheim",
                }
            },
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        result = await crawler.crawl()

        assert result.courses_ok == 1
        assert not any(c[0] == "enrol_self_enrol_user" for c in moodle.calls)

    async def test_non_moodle_links_are_ignored(self) -> None:
        """AC-3"""
        source = {**COURSE, "id": 100}
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100, "Cloud", "https://cloud.itech-bs14.de/index.php/s/AbC123"
                ),
            },
            courses=[source],
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        await crawler.crawl()
        assert not any(c[0] == "core_enrol_get_course_enrolment_methods" for c in moodle.calls)


class TestDirectModuleLinks:
    """Spec 011 AC-14/AC-16: a link to one specific page, not the whole course."""

    async def test_direct_module_link_resolves_and_joins_its_course(self) -> None:
        source = {**COURSE, "id": 100}
        linked = {
            "id": 878,
            "fullname": "Datensicherheit",
            "shortname": "DS",
            "timemodified": 1700000000,
        }
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100,
                    "Lernfeld 8",
                    "https://moodle.itech-bs14.de/mod/page/view.php?id=64055&inpopup=1",
                ),
                878: [
                    {
                        "id": 2,
                        "name": "Start",
                        "visible": 1,
                        "modules": [
                            {
                                "id": 7001,
                                "name": "Info",
                                "modname": "label",
                                "visible": 1,
                                "uservisible": True,
                                "description": "<p>Datenschutz-Inhalte.</p>",
                            },
                        ],
                    }
                ],
            },
            courses=[source],
            modules={64055: 878},
            enrolment_methods={
                878: [{"id": 9001, "courseid": 878, "type": "self", "name": "Self", "status": True}]
            },
            instance_info={
                9001: {"id": 9001, "courseid": 878, "type": "self", "name": "Self", "status": True}
            },
            courses_after_enrol=[source, linked],
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        result = await crawler.crawl()

        assert result.courses_ok == 2
        assert any(i.course_id == 878 for i in result.items)
        assert any("Datenschutz-Inhalte" in (i.text or "") for i in result.items)

    async def test_module_link_to_an_already_known_course_skips_self_enrolment(self) -> None:
        """There is no way to know a cmid's owning course without resolving it
        first — that read-only lookup always happens. What must NOT happen is
        attempting self-enrolment into a course we're already in."""
        source = {**COURSE, "id": 100}
        already = {**COURSE, "id": 878, "fullname": "Bereits da"}
        moodle = LinkedCourseFakeMoodle(
            contents_by_course={
                100: _course_with_link(
                    100,
                    "Lernfeld 8",
                    "https://moodle.itech-bs14.de/mod/page/view.php?id=64055",
                ),
                878: [],
            },
            courses=[source, already],
            modules={64055: 878},
        )
        crawler = CourseCrawler(
            moodle,
            moodle_host="moodle.itech-bs14.de",
            follow_linked_courses=True,  # type: ignore[arg-type]
        )
        result = await crawler.crawl()
        assert any(c[0] == "core_course_get_course_module" for c in moodle.calls)
        assert not any(c[0] == "core_enrol_get_course_enrolment_methods" for c in moodle.calls)
        assert result.courses_ok == 2  # source + already-known, no duplicate join
