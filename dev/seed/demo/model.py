"""A small builder for manifest.json (version 2, see dev/seed/README.md).

Course content modules call ``Dataset.course(...).section(...).page(...)`` and so on;
files are written to ``<dataset>/files/<course>/<module>/`` as they are added, and
``Dataset.manifest()`` returns the finished manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import render

Json = dict[str, Any]

#: A restriction date that never arrives (restricted sections stay greyed out).
NEVER = 4070908800  # 2099-01-01


@dataclass
class Probe:
    """A question the dataset can answer, for trying the assistant by hand."""

    question: str
    answer: str
    source: str
    users: str  # who should get the answer, e.g. "alle", "nur teacher"


@dataclass
class Section:
    course: Course
    name: str
    available_from: int | None = None
    modules: list[Json] = field(default_factory=list)

    # -- helpers ------------------------------------------------------------ #

    def _id(self, key: str) -> str:
        return f"{self.course.idnumber}:{key}"

    def _file(self, key: str, filename: str, data: bytes, filepath: str = "/") -> Json:
        folder = filepath.strip("/")
        relative = Path("files", self.course.shortname, key, folder, filename)
        target = self.course.dataset.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"filepath": filepath, "filename": filename, "path": relative.as_posix()}

    def _add(
        self, modname: str, key: str, name: str, *, intro: str | None, visible: bool, **extra: Any
    ) -> Json:
        module: Json = {"idnumber": self._id(key), "modname": modname, "name": name}
        if intro:
            module["intro"] = render.to_html_fragment(intro)
        if not visible:
            module["visible"] = False
        module.update(extra)
        self.modules.append(module)
        return module

    # -- module types -------------------------------------------------------- #

    def page(
        self,
        key: str,
        name: str,
        content: str,
        *,
        intro: str | None = None,
        images: dict[str, bytes] | None = None,
        raw_html: str = "",
        visible: bool = True,
    ) -> None:
        """A page; ``images`` are embedded files, referenced as ``@@PLUGINFILE@@/name``."""
        html = render.to_html_fragment(content) + raw_html
        files = [self._file(key, n, data) for n, data in (images or {}).items()]
        extra: Json = {"content": html}
        if files:
            extra["files"] = files
        self._add("page", key, name, intro=intro, visible=visible, **extra)

    def book(
        self,
        key: str,
        name: str,
        chapters: list[tuple[str, str]],
        *,
        intro: str | None = None,
        visible: bool = True,
    ) -> None:
        self._add(
            "book",
            key,
            name,
            intro=intro,
            visible=visible,
            chapters=[
                {"title": title, "content": render.to_html_fragment(body)}
                for title, body in chapters
            ],
        )

    def resource(
        self,
        key: str,
        name: str,
        filename: str,
        data: bytes,
        *,
        intro: str | None = None,
        visible: bool = True,
    ) -> None:
        self._add(
            "resource",
            key,
            name,
            intro=intro,
            visible=visible,
            files=[self._file(key, filename, data)],
        )

    def folder(
        self,
        key: str,
        name: str,
        files: list[tuple[str, str, bytes]],
        *,
        intro: str | None = None,
        visible: bool = True,
    ) -> None:
        """``files``: (sub-path such as "/" or "/Woche 1/", filename, data)."""
        self._add(
            "folder",
            key,
            name,
            intro=intro,
            visible=visible,
            files=[self._file(key, fn, data, path) for path, fn, data in files],
        )

    def url(
        self, key: str, name: str, url: str, *, intro: str | None = None, visible: bool = True
    ) -> None:
        self._add("url", key, name, intro=intro, visible=visible, externalurl=url)

    def label(self, key: str, text: str, *, visible: bool = True) -> None:
        name = render.plain(render.parse(text)[0].text)[:60]
        self._add("label", key, name, intro=text, visible=visible)

    def activity(
        self, modname: str, key: str, name: str, intro: str, *, visible: bool = True
    ) -> None:
        """assign, quiz, forum, choice, feedback: only their description is seeded."""
        self._add(modname, key, name, intro=intro, visible=visible)


@dataclass
class Course:
    dataset: Dataset
    idnumber: str
    fullname: str
    shortname: str
    enrol: dict[str, str]
    sections: list[Section] = field(default_factory=list)

    def section(self, name: str, *, available_from: int | None = None) -> Section:
        section = Section(self, name, available_from)
        self.sections.append(section)
        return section


@dataclass
class Dataset:
    root: Path
    prefix: str
    category: str
    users: list[Json]
    courses: list[Course] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)

    def course(self, key: str, fullname: str, enrol: dict[str, str]) -> Course:
        course = Course(self, f"{self.prefix}:{key}", fullname, f"{self.prefix}-{key}", enrol)
        self.courses.append(course)
        return course

    def probe(self, question: str, answer: str, source: str, users: str = "alle") -> None:
        self.probes.append(Probe(question, answer, source, users))

    def manifest(self) -> Json:
        courses = []
        for c in self.courses:
            sections = []
            for s in c.sections:
                section: Json = {"name": s.name, "modules": s.modules}
                if s.available_from is not None:
                    section["available_from"] = s.available_from
                sections.append(section)
            courses.append(
                {
                    "idnumber": c.idnumber,
                    "fullname": c.fullname,
                    "shortname": c.shortname,
                    "enrol": c.enrol,
                    "sections": sections,
                }
            )
        return {
            "version": 2,
            "prefix": self.prefix,
            "category": self.category,
            "users": self.users,
            "courses": courses,
            "backups": [],
            "restore_users": False,
            "skipped": [],
        }
