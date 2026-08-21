"""Course structure crawler — see ``specs/003-content-model.md``.

Discovery only: this walks the course tree and emits :class:`ContentItem`s. Nothing
is downloaded here, so a full crawl of all 19 courses costs 20 API calls and no
bandwidth, which is what makes frequent freshness checks cheap.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from bsbot.ingest.model import (
    ContentItem,
    ContentKind,
    FileRef,
    classify_file,
    clean_title,
    html_to_text,
)
from bsbot.moodle.enrolment import (
    await_enrolled_course,
    extract_linked_course_id,
    extract_linked_module_cmid,
    resolve_module_course,
    try_self_enrol,
)

log = structlog.get_logger(__name__)

#: 25 MB. On the live site this excludes 3 files totalling 123 MB while keeping 512.
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024

#: Modules that are containers or interactive-only: no text of their own.
_NO_CONTENT_MODULES = frozenset({"subsection", "bigbluebuttonbn", "attendance", "scheduler"})


class MoodleSource(Protocol):
    """The port the crawler needs — see spec 002. Lets tests supply a fake."""

    async def site_info(self) -> Any: ...
    async def call(self, wsfunction: str, **params: Any) -> Any: ...


@dataclass
class CrawlResult:
    items: list[ContentItem] = field(default_factory=list)
    courses_ok: int = 0
    courses_failed: int = 0


@dataclass
class CrawlDelta:
    added: set[str] = field(default_factory=set)
    changed: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)


class CourseCrawler:
    def __init__(
        self,
        moodle: MoodleSource,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        course_allowlist: set[int] | None = None,
        moodle_host: str | None = None,
        follow_linked_courses: bool = False,
    ) -> None:
        self._moodle = moodle
        self._max_file_bytes = max_file_bytes
        self._allowlist = course_allowlist
        #: See specs/010-linked-course-discovery.md. Off by default: this can enrol
        #: the account into a course, a real and visible action, so it only runs
        #: when explicitly requested.
        self._moodle_host = moodle_host
        self._follow_linked = follow_linked_courses and moodle_host is not None

    async def crawl(self) -> CrawlResult:
        info = await self._moodle.site_info()
        courses = await self._moodle.call("core_enrol_get_users_courses", userid=info.userid)
        if self._allowlist is not None:
            courses = [c for c in courses if c["id"] in self._allowlist]

        gathered = await asyncio.gather(
            *(self._fetch_course(course) for course in courses), return_exceptions=True
        )

        result = CrawlResult()
        for course, outcome in zip(courses, gathered, strict=True):
            if isinstance(outcome, BaseException):
                # AC-1: one broken course must not lose the whole sync.
                result.courses_failed += 1
                log.warning(
                    "crawl.course.failed",
                    course_id=course.get("id"),
                    course=course.get("shortname"),
                    error=f"{type(outcome).__name__}: {outcome}",
                )
                continue
            result.courses_ok += 1
            result.items.extend(outcome)

        if self._follow_linked:
            known_ids = {c["id"] for c in courses}
            await self._follow_linked_courses(result, known_ids)

        log.info(
            "crawl.done",
            courses_ok=result.courses_ok,
            courses_failed=result.courses_failed,
            items=len(result.items),
        )
        return result

    async def _follow_linked_courses(self, result: CrawlResult, known_ids: set[int]) -> None:
        """Discover same-host course links, self-enrol where safe, and crawl them too.

        Runs after the main crawl, over its own output — link discovery needs the
        already-extracted ContentItems (AC-1), and a freshly-enrolled course needs
        its fullname, which only a second core_enrol_get_users_courses call can give
        us (core_course_get_courses needs permissions this account does not have,
        verified live) (AC-9).
        """
        assert self._moodle_host is not None
        candidates: set[int] = set()
        module_cmids: set[int] = set()
        for item in result.items:
            if item.kind is not ContentKind.EXTERNAL or not item.external_url:
                continue
            course_id = extract_linked_course_id(item.external_url, moodle_host=self._moodle_host)
            if course_id is not None and course_id not in known_ids:  # AC-2
                candidates.add(course_id)
                continue
            # Spec 011 AC-14: a link to one specific page, not the whole course.
            cmid = extract_linked_module_cmid(item.external_url, moodle_host=self._moodle_host)
            if cmid is not None:
                module_cmids.add(cmid)

        for cmid in module_cmids:
            module_course_id = await resolve_module_course(self._moodle, cmid)
            if module_course_id is not None and module_course_id not in known_ids:
                candidates.add(module_course_id)

        for course_id in candidates:
            enrolled = await try_self_enrol(self._moodle, course_id)
            if not enrolled:
                continue
            info = await self._moodle.site_info()
            # Verified live: a successful enrol_self_enrol_user does not appear in
            # core_enrol_get_users_courses immediately — a single re-query would
            # silently drop every course this feature just enrolled into.
            course = await await_enrolled_course(
                self._moodle, userid=info.userid, courseid=course_id
            )
            if course is None:
                log.warning("crawl.linked_course.vanished", course_id=course_id)
                continue
            try:
                items = await self._fetch_course(course)
            except Exception as exc:
                result.courses_failed += 1
                log.warning(
                    "crawl.course.failed",
                    course_id=course_id,
                    course=course.get("shortname"),
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            result.courses_ok += 1
            result.items.extend(items)
            log.info("crawl.linked_course.joined", course_id=course_id, items=len(items))

    async def _fetch_course(self, course: dict[str, Any]) -> list[ContentItem]:
        sections = await self._moodle.call("core_course_get_contents", courseid=course["id"])
        course_name = course.get("fullname") or course.get("shortname") or str(course["id"])
        items: list[ContentItem] = []
        for section in sections:
            items.extend(self._walk_section(course["id"], course_name, section))
        return items

    def _walk_section(
        self, course_id: int, course_name: str, section: dict[str, Any]
    ) -> list[ContentItem]:
        section_name = clean_title(section.get("name"))
        items: list[ContentItem] = []
        for module in section.get("modules", []) or []:
            # AC-12: never surface content the student cannot see.
            if module.get("visible") == 0 or module.get("uservisible") is False:
                continue
            items.extend(self._walk_module(course_id, course_name, section_name, module))
        return items

    def _walk_module(
        self, course_id: int, course_name: str, section_name: str, module: dict[str, Any]
    ) -> list[ContentItem]:
        modname = module.get("modname", "")
        module_id = int(module["id"])
        module_name = clean_title(module.get("name"), fallback=modname)
        base = {
            "course_id": course_id,
            "course_name": course_name,
            "section_name": section_name,
            "module_id": module_id,
            "module_name": module_name,
            "modname": modname,
            "module_url": module.get("url"),
        }
        path = [course_name, section_name, module_name]
        mtime = self._module_mtime(module)
        items: list[ContentItem] = []

        # A label *is* its description; every other module's description is a separate
        # intro item so it does not compete with the module's real content (AC-5, AC-6).
        description = html_to_text(module.get("description"))
        if description:
            is_label = modname == "label"
            items.append(
                ContentItem(
                    **base,
                    doc_id=f"{course_id}:{module_id}:{'0' if is_label else 'intro'}",
                    title=module_name,
                    kind=ContentKind.INLINE,
                    header_path=path,
                    text=description,
                    timemodified=mtime,
                )
            )

        if modname in _NO_CONTENT_MODULES:  # AC-11
            return items

        contents = module.get("contents") or []
        if modname == "book":
            items.extend(self._walk_book(base, path, contents))
            return items

        ordinal = 0
        for entry in contents:
            entry_type = entry.get("type")
            if entry_type == "url":  # AC-10
                target = entry.get("fileurl")
                if not target:
                    continue
                items.append(
                    ContentItem(
                        **base,
                        doc_id=f"{course_id}:{module_id}:{ordinal}",
                        title=clean_title(entry.get("filename"), fallback=module_name),
                        kind=ContentKind.EXTERNAL,
                        header_path=path,
                        # Moodle HTML-escapes query separators in these URLs.
                        external_url=html_lib.unescape(target),
                        timemodified=entry.get("timemodified") or mtime,
                    )
                )
                ordinal += 1
            elif entry_type == "file":
                item = self._file_item(base, path, entry, ordinal, mtime)
                if item is not None:
                    items.append(item)
                    ordinal += 1
        return items

    def _file_item(
        self,
        base: dict[str, Any],
        path: list[str],
        entry: dict[str, Any],
        ordinal: int,
        mtime: int,
    ) -> ContentItem | None:
        url = entry.get("fileurl")
        if not url:
            return None
        ref = FileRef(
            url=url,
            filename=entry.get("filename") or "",
            filesize=int(entry.get("filesize") or 0),
            mimetype=entry.get("mimetype"),
            timemodified=int(entry.get("timemodified") or 0),
        )
        # Generated Moodle HTML (page, book) is prose, not an attachment, and its
        # reported size of 0 must never be size-filtered (AC-7, AC-14).
        is_generated_html = ref.filename == "index.html"
        if is_generated_html:
            extractable, reason = True, None
            kind = ContentKind.HTML
        else:
            extractable, reason = classify_file(ref, self._max_file_bytes)
            kind = ContentKind.FILE

        return ContentItem(
            **base,
            doc_id=f"{base['course_id']}:{base['module_id']}:{ordinal}",
            title=clean_title(ref.filename, fallback=base["module_name"]),
            kind=kind,
            header_path=path,
            file=ref,
            timemodified=ref.timemodified or mtime,
            extractable=extractable,
            skip_reason=reason,
        )

    def _walk_book(
        self, base: dict[str, Any], path: list[str], contents: list[dict[str, Any]]
    ) -> list[ContentItem]:
        """Emit one item per chapter so citations name the chapter (AC-8).

        Moodle ships chapter titles in a separate ``structure`` entry and the chapter
        bodies as ``/<chapterid>/index.html`` files, in matching order.
        """
        titles: list[str] = []
        for entry in contents:
            if entry.get("type") == "content" and entry.get("filename") == "structure":
                try:
                    titles = [c.get("title", "") for c in json.loads(entry.get("content") or "[]")]
                except (ValueError, AttributeError):
                    titles = []
                break

        chapters = [
            e for e in contents if e.get("type") == "file" and e.get("filename") == "index.html"
        ]
        items: list[ContentItem] = []
        for ordinal, entry in enumerate(chapters):
            title = clean_title(
                titles[ordinal] if ordinal < len(titles) else "",
                fallback=f"Kapitel {ordinal + 1}",
            )
            items.append(
                ContentItem(
                    **base,
                    doc_id=f"{base['course_id']}:{base['module_id']}:{ordinal}",
                    title=title,
                    kind=ContentKind.HTML,
                    header_path=[*path, title],
                    file=FileRef(
                        url=entry["fileurl"],
                        filename=entry.get("filename") or "index.html",
                        filesize=0,
                        mimetype="text/html",
                        timemodified=int(entry.get("timemodified") or 0),
                    ),
                    timemodified=int(entry.get("timemodified") or 0),
                )
            )
        return items

    @staticmethod
    def _module_mtime(module: dict[str, Any]) -> int:
        """Prefer ``contentsinfo.lastmodified`` — the cheapest change signal (AC-16)."""
        info = module.get("contentsinfo") or {}
        return int(info.get("lastmodified") or module.get("timemodified") or 0)


def diff_crawls(before: list[ContentItem], after: list[ContentItem]) -> CrawlDelta:
    """Compare two crawls so removed content can be tombstoned (AC-17)."""
    old = {i.doc_id: i for i in before}
    new = {i.doc_id: i for i in after}
    changed = {
        doc_id
        for doc_id in old.keys() & new.keys()
        if old[doc_id].timemodified != new[doc_id].timemodified
    }
    return CrawlDelta(
        added=new.keys() - old.keys(), changed=changed, removed=old.keys() - new.keys()
    )
