"""dev/seed/build_manifest.py: the legacy content index becomes a Moodle seed manifest.

The legacy index (Matrix-era bsbot) stored one row per *document*, not per Moodle
module: a folder has one row per file, a book one per chapter, an intro its own row.
The manifest regroups them into courses → sections → modules the dev Moodle can
recreate with its own generators.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_manifest", ROOT / "dev" / "seed" / "build_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_manifest"] = module
    spec.loader.exec_module(module)
    return module


bm = _load()

SITE = "https://moodle.example.invalid"


class Legacy:
    """A legacy dataset directory: documents.db plus blobs/<aa>/<sha256>."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "blobs").mkdir(parents=True)
        self.db = sqlite3.connect(root / "documents.db")
        self.db.execute(
            """CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY, course_id INTEGER, course_name TEXT,
                section_name TEXT, module_id INTEGER, module_name TEXT, modname TEXT,
                title TEXT, kind TEXT, text TEXT, file_url TEXT, filename TEXT,
                filesize INTEGER DEFAULT 0, mimetype TEXT, external_url TEXT,
                blob_sha256 TEXT, tombstoned_at INTEGER)"""
        )

    def blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        (self.root / "blobs" / sha[:2]).mkdir(exist_ok=True)
        (self.root / "blobs" / sha[:2] / sha).write_bytes(data)
        return sha

    def doc(self, doc_id: str, **fields: Any) -> None:
        course, cmid, _ = doc_id.split(":")
        row = {
            "doc_id": doc_id,
            "course_id": int(course),
            "course_name": f"Course {course}",
            "section_name": "Allgemeines",
            "module_id": int(cmid),
            "module_name": f"Module {cmid}",
            "modname": "page",
            "title": "",
            "kind": "inline",
            **fields,
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.db.execute(f"INSERT INTO documents ({cols}) VALUES ({marks})", list(row.values()))
        self.db.commit()

    def build(self, config: str | None = None) -> dict[str, Any]:
        parsed = bm.parse_config(config) if config is not None else None
        manifest: dict[str, Any] = bm.build_manifest(self.root, parsed)
        return manifest

    def backup(self, name: str, data: bytes = b"mbz") -> Path:
        (self.root / "backups").mkdir(exist_ok=True)
        path = self.root / "backups" / name
        path.write_bytes(data)
        return path


@pytest.fixture
def legacy(tmp_path: Path) -> Legacy:
    return Legacy(tmp_path / "dataset")


def file_url(context: int, component: str, path: str) -> str:
    return (
        f"{SITE}/webservice/pluginfile.php/{context}/{component}/content/3/{path}?forcedownload=1"
    )


def only_module(manifest: dict[str, Any]) -> dict[str, Any]:
    [course] = manifest["courses"]
    [section] = course["sections"]
    [module] = section["modules"]
    return module


class TestStructure:
    def test_courses_sections_and_modules_keep_first_seen_order(self, legacy: Legacy) -> None:
        # Insertion order is the order the legacy crawler walked the course.
        legacy.doc("7:30:intro", section_name="Zweitens", text="b")
        legacy.doc("7:10:intro", section_name="Erstens", text="a")
        legacy.doc("7:20:intro", section_name="Zweitens", text="c")
        legacy.doc("5:40:intro", course_name="Andere", text="d")

        courses = legacy.build()["courses"]

        assert [c["legacy_id"] for c in courses] == [5, 7]
        [_, course] = courses
        assert course["fullname"] == "Course 7"
        assert [s["name"] for s in course["sections"]] == ["Zweitens", "Erstens"]
        assert [m["legacy_cmid"] for m in course["sections"][0]["modules"]] == [30, 20]

    def test_tombstoned_documents_and_pseudo_courses_are_dropped(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", text="still there")
        legacy.doc("7:11:intro", text="deleted in Moodle", tombstoned_at=123)
        legacy.doc("0:12:intro", course_name="Matrix: room", text="not a Moodle course")

        manifest = legacy.build()

        assert [c["legacy_id"] for c in manifest["courses"]] == [7]
        assert [m["legacy_cmid"] for m in manifest["courses"][0]["sections"][0]["modules"]] == [10]

    def test_names_are_html_unescaped(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="assign", module_name="Q&amp;A Board", text="x")

        assert only_module(legacy.build())["name"] == "Q&A Board"

    def test_enrolments_leave_every_user_some_course_they_cannot_see(self, legacy: Legacy) -> None:
        for course in range(1, 9):
            legacy.doc(f"{course}:{course}0:intro", text="x")

        courses = legacy.build()["courses"]

        def enrolled(user: str) -> int:
            return sum(user in c["enrol"] for c in courses)

        assert all(c["enrol"]["teacher"] == "editingteacher" for c in courses)
        assert 0 < enrolled("student") < len(courses)
        assert 0 < enrolled("student2") < len(courses)
        assert any("student" in c["enrol"] and "student2" not in c["enrol"] for c in courses)


class TestAccessTraps:
    """Each course hides something from students: a module, or a whole section."""

    @staticmethod
    def seed(legacy: Legacy, courses: int, sections: int) -> list[dict[str, Any]]:
        for course in range(1, courses + 1):
            for section in range(sections):
                cmid = course * 100 + section * 10
                legacy.doc(
                    f"{course}:{cmid}:intro",
                    modname="label",
                    section_name=f"S{section}",
                    text="Hinweis",
                )
                legacy.doc(
                    f"{course}:{cmid + 1}:0",
                    modname="page",
                    kind="html",
                    section_name=f"S{section}",
                    blob_sha256=legacy.blob(b"<p>x</p>"),
                )
        courses_out: list[dict[str, Any]] = legacy.build()["courses"]
        return courses_out

    @staticmethod
    def hidden(course: dict[str, Any]) -> list[dict[str, Any]]:
        return [m for s in course["sections"] for m in s["modules"] if m.get("visible") is False]

    @staticmethod
    def restricted(course: dict[str, Any]) -> list[dict[str, Any]]:
        return [s for s in course["sections"] if "available_from" in s]

    def test_every_course_gets_exactly_one_trap_and_both_kinds_occur(self, legacy: Legacy) -> None:
        courses = self.seed(legacy, courses=4, sections=3)

        assert all(len(self.hidden(c)) + len(self.restricted(c)) == 1 for c in courses)
        assert any(self.hidden(c) for c in courses)
        assert any(self.restricted(c) for c in courses)

    def test_the_hidden_module_is_content_not_a_label(self, legacy: Legacy) -> None:
        [course] = self.seed(legacy, courses=1, sections=2)

        [module] = self.hidden(course)
        assert module["modname"] == "page"
        assert module is course["sections"][-1]["modules"][-1]

    def test_the_restricted_section_is_never_the_general_one_and_opens_in_the_future(
        self, legacy: Legacy
    ) -> None:
        courses = self.seed(legacy, courses=4, sections=3)

        for course in courses:
            for section in self.restricted(course):
                assert section is not course["sections"][0]
                assert section["available_from"] > 4_000_000_000  # long after any test run

    def test_a_single_section_course_hides_a_module_instead(self, legacy: Legacy) -> None:
        courses = self.seed(legacy, courses=4, sections=1)

        assert all(len(self.hidden(c)) == 1 and not self.restricted(c) for c in courses)


class TestModules:
    def test_inline_text_becomes_escaped_paragraphs(self, legacy: Legacy) -> None:
        legacy.doc(
            "7:10:0", modname="label", kind="inline", text="Erste <Zeile>\nzweite\n\nNeu & so"
        )

        module = only_module(legacy.build())

        assert module["modname"] == "label"
        assert module["intro"] == "<p>Erste &lt;Zeile&gt;<br>\nzweite</p>\n<p>Neu &amp; so</p>"

    def test_page_content_comes_from_its_blob_and_embeds_its_files(self, legacy: Legacy) -> None:
        html = b'<p>Siehe <img src="vier%20Ohren.png"> und <a href="https://x.invalid/a">x</a></p>'
        legacy.doc("7:10:intro", text="Worum es geht")
        legacy.doc("7:10:1", kind="html", blob_sha256=legacy.blob(html))
        png = legacy.blob(b"\x89PNG")
        legacy.doc(
            "7:10:2",
            kind="file",
            filename="vier Ohren.png",
            mimetype="image/png",
            file_url=file_url(99, "mod_page", "vier%20Ohren.png"),
            blob_sha256=png,
        )

        module = only_module(legacy.build())

        assert module["intro"] == "<p>Worum es geht</p>"
        assert module["content"] == (
            '<p>Siehe <img src="@@PLUGINFILE@@/vier%20Ohren.png"> '
            'und <a href="https://x.invalid/a">x</a></p>'
        )
        assert module["files"] == [{"filepath": "/", "filename": "vier Ohren.png", "blob": png}]

    def test_book_chapters_in_order(self, legacy: Legacy) -> None:
        legacy.doc(
            "7:10:0",
            modname="book",
            kind="html",
            title="Einleitung",
            blob_sha256=legacy.blob(b"<p>eins</p>"),
        )
        legacy.doc(
            "7:10:1",
            modname="book",
            kind="html",
            title="Hauptteil",
            blob_sha256=legacy.blob(b"<p>zwei</p>"),
        )

        module = only_module(legacy.build())

        assert module["modname"] == "book"
        assert module["chapters"] == [
            {"title": "Einleitung", "content": "<p>eins</p>"},
            {"title": "Hauptteil", "content": "<p>zwei</p>"},
        ]

    def test_folder_keeps_subdirectories(self, legacy: Legacy) -> None:
        a = legacy.blob(b"a")
        b = legacy.blob(b"b")
        legacy.doc(
            "7:10:0",
            modname="folder",
            kind="file",
            filename="Plan.pdf",
            file_url=file_url(9, "mod_folder", "Plan.pdf"),
            blob_sha256=a,
        )
        legacy.doc(
            "7:10:1",
            modname="folder",
            kind="file",
            filename="Lösung 1.pdf",
            file_url=file_url(9, "mod_folder", "Woche%201/L%C3%B6sung%201.pdf"),
            blob_sha256=b,
        )

        module = only_module(legacy.build())

        assert module["files"] == [
            {"filepath": "/", "filename": "Plan.pdf", "blob": a},
            {"filepath": "/Woche 1/", "filename": "Lösung 1.pdf", "blob": b},
        ]

    def test_url_keeps_external_url_and_intro(self, legacy: Legacy) -> None:
        legacy.doc("7:10:0", modname="url", kind="external", external_url="https://x.invalid/")
        legacy.doc("7:10:intro", modname="url", kind="inline", text="Lies das")

        module = only_module(legacy.build())

        assert module["externalurl"] == "https://x.invalid/"
        assert module["intro"] == "<p>Lies das</p>"

    @pytest.mark.parametrize("modname", ["assign", "quiz", "forum", "choice", "feedback"])
    def test_activities_keep_their_description(self, legacy: Legacy, modname: str) -> None:
        legacy.doc("7:10:intro", modname=modname, kind="inline", text="Abgabe bis Freitag")

        module = only_module(legacy.build())

        assert module["modname"] == modname
        assert module["intro"] == "<p>Abgabe bis Freitag</p>"


class TestGaps:
    def test_files_without_a_blob_are_reported_not_seeded(self, legacy: Legacy) -> None:
        a = legacy.blob(b"a")
        legacy.doc(
            "7:10:0",
            modname="folder",
            kind="file",
            filename="ok.pdf",
            file_url=file_url(9, "mod_folder", "ok.pdf"),
            blob_sha256=a,
        )
        legacy.doc(
            "7:10:1",
            modname="folder",
            kind="file",
            filename="big.mp4",
            file_url=file_url(9, "mod_folder", "big.mp4"),
        )
        legacy.doc(
            "7:10:2",
            modname="folder",
            kind="file",
            filename="gone.pdf",
            file_url=file_url(9, "mod_folder", "gone.pdf"),
            blob_sha256="0" * 64,
        )

        manifest = legacy.build()

        assert [f["filename"] for f in only_module(manifest)["files"]] == ["ok.pdf"]
        assert sorted(s["reason"] for s in manifest["skipped"]) == ["blob missing", "no blob"]

    def test_a_resource_with_no_file_left_is_skipped(self, legacy: Legacy) -> None:
        legacy.doc("7:10:0", modname="resource", kind="file", filename="x.docx")
        legacy.doc("7:11:intro", modname="label", text="bleibt")

        manifest = legacy.build()

        assert [m["legacy_cmid"] for m in manifest["courses"][0]["sections"][0]["modules"]] == [11]
        assert {"cmid": 10, "reason": "no content"} in [
            {"cmid": s["cmid"], "reason": s["reason"]} for s in manifest["skipped"]
        ]

    def test_unknown_module_types_are_skipped(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="hvp", text="interaktiv")

        manifest = legacy.build()

        assert manifest["courses"] == []
        assert manifest["skipped"][0]["reason"] == "unsupported modname hvp"


def test_main_writes_manifest_and_summary(
    legacy: Legacy, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy.doc("7:10:intro", modname="label", text="x")

    assert bm.main([str(legacy.root)]) == 0

    manifest = json.loads((legacy.root / "manifest.json").read_text())
    assert manifest["courses"][0]["legacy_id"] == 7
    assert "1 courses, 1 modules" in capsys.readouterr().out


def courses_of(manifest: dict[str, Any]) -> list[str]:
    return [c["idnumber"] for c in manifest["courses"]]


class TestDefaults:
    """Without a seed.toml the manifest is what the dev stack has always seeded."""

    def test_ids_category_and_users(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")

        manifest = legacy.build()

        assert manifest["version"] == 2
        assert manifest["prefix"] == "legacy"
        assert manifest["category"] == "Legacy dataset"
        [course] = manifest["courses"]
        assert (course["idnumber"], course["shortname"]) == ("legacy:7", "legacy-7")
        assert only_module(manifest)["idnumber"] == "legacy:10"
        assert [u["username"] for u in manifest["users"]] == ["teacher", "student", "student2"]
        assert manifest["backups"] == []
        assert manifest["restore_users"] is False


class TestConfig:
    def test_prefix_and_category_keep_datasets_apart(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")

        manifest = legacy.build('prefix = "demo"\ncategory = "Demo"')

        assert courses_of(manifest) == ["demo:7"]
        assert manifest["courses"][0]["shortname"] == "demo-7"
        assert only_module(manifest)["idnumber"] == "demo:10"
        assert manifest["category"] == "Demo"

    def test_category_defaults_to_the_prefix(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")

        assert legacy.build('prefix = "demo"')["category"] == "demo dataset"

    @pytest.mark.parametrize("prefix", ["", "has space", "a:b", "x" * 40])
    def test_prefix_must_be_a_short_slug(self, prefix: str) -> None:
        with pytest.raises(bm.ConfigError, match="prefix"):
            bm.parse_config(f'prefix = "{prefix}"')

    def test_course_subset(self, legacy: Legacy) -> None:
        for course in (5, 7, 9):
            legacy.doc(f"{course}:{course}0:intro", modname="label", text="x")

        assert courses_of(legacy.build("courses = [9, 5]")) == ["legacy:5", "legacy:9"]

    def test_files_over_the_size_cap_are_skipped(self, legacy: Legacy) -> None:
        small = legacy.blob(b"a")
        big = legacy.blob(b"b" * 2_000_000)
        legacy.doc(
            "7:10:0",
            modname="folder",
            kind="file",
            filename="small.pdf",
            file_url=file_url(9, "mod_folder", "small.pdf"),
            blob_sha256=small,
        )
        legacy.doc(
            "7:10:1",
            modname="folder",
            kind="file",
            filename="big.pdf",
            file_url=file_url(9, "mod_folder", "big.pdf"),
            blob_sha256=big,
        )

        manifest = legacy.build("max_file_mb = 1")

        assert [f["filename"] for f in only_module(manifest)["files"]] == ["small.pdf"]
        assert [s["reason"] for s in manifest["skipped"]] == ["too large"]

    @pytest.mark.parametrize(
        ("mode", "hidden", "restricted"),
        [("none", 0, 0), ("hidden", 4, 0), ("restricted", 0, 4), ("alternate", 2, 2)],
    )
    def test_trap_modes(self, legacy: Legacy, mode: str, hidden: int, restricted: int) -> None:
        for course in range(1, 5):
            for section in range(2):
                legacy.doc(
                    f"{course}:{course}{section}:0",
                    modname="page",
                    kind="html",
                    section_name=f"S{section}",
                    blob_sha256=legacy.blob(b"<p>x</p>"),
                )

        courses = legacy.build(f'traps = "{mode}"')["courses"]

        assert sum(len(TestAccessTraps.hidden(c)) for c in courses) == hidden
        assert sum(len(TestAccessTraps.restricted(c)) for c in courses) == restricted

    def test_users_replace_the_defaults_and_follow_their_pattern(self, legacy: Legacy) -> None:
        for course in range(1, 5):
            legacy.doc(f"{course}:{course}0:intro", modname="label", text="x")

        manifest = legacy.build(
            '[users.lehrer]\nrole = "editingteacher"\n'
            '[users.anna]\nrole = "student"\npattern = "01"\nfirstname = "Anna"\n'
        )

        assert manifest["users"] == [
            {"username": "lehrer", "firstname": "Lehrer"},
            {"username": "anna", "firstname": "Anna"},
        ]
        assert [c["enrol"] for c in manifest["courses"]] == [
            {"lehrer": "editingteacher"},
            {"lehrer": "editingteacher", "anna": "student"},
            {"lehrer": "editingteacher"},
            {"lehrer": "editingteacher", "anna": "student"},
        ]

    @pytest.mark.parametrize(
        "toml",
        [
            "trapz = 'none'",  # a typo must not silently fall back to the default
            "traps = 'sometimes'",
            "max_file_mb = -1",
            "courses = 'all'",
            "[users.x]\nrole = 'student'\npattern = '12'",
            "[users.x]\npattern = '1'",
            "[users.x]\nrole = 'student'\ncolour = 'red'",
            "[users]\n",
        ],
    )
    def test_invalid_config_is_rejected(self, toml: str) -> None:
        with pytest.raises(bm.ConfigError):
            bm.parse_config(toml)


class TestBackups:
    """Moodle course backups (.mbz) in backups/ are restored as they are."""

    def test_backups_follow_the_generated_courses_and_their_enrolment_pattern(
        self, legacy: Legacy
    ) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")
        b = legacy.backup("b-netzwerke.mbz", b"second")
        a = legacy.backup("a-englisch.mbz", b"first")
        (legacy.root / "backups" / "notes.txt").write_text("ignored")

        manifest = legacy.build('[users.s]\nrole = "student"\npattern = "10"')

        assert [x["file"] for x in manifest["backups"]] == [
            "backups/a-englisch.mbz",
            "backups/b-netzwerke.mbz",
        ]
        sha = hashlib.sha256(a.read_bytes()).hexdigest()
        assert manifest["backups"][0]["idnumber"] == f"legacy:mbz:{sha[:16]}"
        assert manifest["backups"][1]["idnumber"] != manifest["backups"][0]["idnumber"]
        assert b.exists()
        # Index 0 is the generated course, so the backups are courses 1 and 2.
        assert [x["enrol"] for x in manifest["backups"]] == [{}, {"s": "student"}]

    def test_a_dataset_of_only_backups(self, legacy: Legacy) -> None:
        legacy.db.close()
        (legacy.root / "documents.db").unlink()
        legacy.backup("kurs.mbz")

        manifest = legacy.build("restore_users = true")

        assert manifest["courses"] == []
        assert len(manifest["backups"]) == 1
        assert manifest["restore_users"] is True

    def test_an_empty_dataset_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(bm.ConfigError, match="nothing to seed"):
            bm.build_manifest(tmp_path)


class TestCheck:
    """--check validates a manifest written by hand or by another converter."""

    @staticmethod
    def manifest(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "version": 2,
            "prefix": "mine",
            "category": "Mine",
            "users": [{"username": "teacher", "firstname": "Tina"}],
            "courses": [
                {
                    "idnumber": "mine:1",
                    "fullname": "Kurs",
                    "shortname": "mine-1",
                    "enrol": {"teacher": "editingteacher"},
                    "sections": [
                        {
                            "name": "Allgemeines",
                            "modules": [
                                {
                                    "idnumber": "mine:1:a",
                                    "modname": "label",
                                    "name": "x",
                                    "intro": "<p>Hallo</p>",
                                },
                                {
                                    "idnumber": "mine:1:b",
                                    "modname": "resource",
                                    "name": "Plan",
                                    "files": [
                                        {
                                            "filepath": "/",
                                            "filename": "plan.pdf",
                                            "path": "files/plan.pdf",
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
            "backups": [],
            "restore_users": False,
        }
        base.update(overrides)
        return base

    @pytest.fixture
    def root(self, tmp_path: Path) -> Path:
        (tmp_path / "files").mkdir()
        (tmp_path / "files" / "plan.pdf").write_bytes(b"%PDF")
        return tmp_path

    def test_a_hand_written_manifest_with_plain_file_paths_passes(self, root: Path) -> None:
        assert bm.check_manifest(self.manifest(), root) == []

    def test_a_course_and_a_module_may_share_an_idnumber(self, root: Path) -> None:
        manifest = self.manifest()
        manifest["courses"][0]["sections"][0]["modules"][0]["idnumber"] = "mine:1"

        assert bm.check_manifest(manifest, root) == []

    def test_the_built_manifest_passes(self, legacy: Legacy) -> None:
        legacy.doc(
            "7:10:0",
            modname="resource",
            kind="file",
            filename="a.pdf",
            file_url=file_url(9, "mod_resource", "a.pdf"),
            blob_sha256=legacy.blob(b"a"),
        )
        legacy.backup("k.mbz")

        assert bm.check_manifest(legacy.build(), legacy.root) == []

    @pytest.mark.parametrize(
        ("change", "error"),
        [
            (lambda m: m.update(version=1), "version"),
            (lambda m: m["courses"][0]["enrol"].update(nobody="student"), "unknown user nobody"),
            (
                lambda m: m["courses"][0]["sections"][0]["modules"][0].update(modname="hvp"),
                "unsupported modname hvp",
            ),
            (
                lambda m: m["courses"][0]["sections"][0]["modules"][1].update(idnumber="mine:1:a"),
                "duplicate idnumber mine:1:a",
            ),
            (
                lambda m: m["courses"][0]["sections"][0]["modules"][1]["files"][0].update(
                    path="files/missing.pdf"
                ),
                "missing file",
            ),
            (
                lambda m: m["courses"][0]["sections"][0]["modules"][1]["files"][0].update(
                    path="../outside.pdf"
                ),
                "outside the dataset",
            ),
            (lambda m: m["courses"][0]["sections"][0]["modules"][1].pop("files"), "needs files"),
            (lambda m: m["courses"][0].pop("shortname"), "shortname"),
            (
                lambda m: m.update(
                    backups=[{"file": "backups/x.mbz", "idnumber": "mine:x", "enrol": {}}]
                ),
                "missing file",
            ),
        ],
    )
    def test_mistakes_are_reported(self, root: Path, change: Any, error: str) -> None:
        manifest = self.manifest()
        change(manifest)

        errors = bm.check_manifest(manifest, root)

        assert any(error in e for e in errors), errors


class TestMain:
    def test_seed_toml_next_to_the_dataset_is_used(self, legacy: Legacy) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")
        (legacy.root / "seed.toml").write_text('prefix = "demo"')

        assert bm.main([str(legacy.root)]) == 0

        assert courses_of(json.loads((legacy.root / "manifest.json").read_text())) == ["demo:7"]

    def test_explicit_config(self, legacy: Legacy, tmp_path: Path) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")
        (tmp_path / "other.toml").write_text('prefix = "other"')

        assert bm.main([str(legacy.root), "--config", str(tmp_path / "other.toml")]) == 0

        assert courses_of(json.loads((legacy.root / "manifest.json").read_text())) == ["other:7"]

    def test_bad_config_fails_without_writing(
        self, legacy: Legacy, capsys: pytest.CaptureFixture[str]
    ) -> None:
        legacy.doc("7:10:intro", modname="label", text="x")
        (legacy.root / "seed.toml").write_text('traps = "sometimes"')

        assert bm.main([str(legacy.root)]) == 2

        assert not (legacy.root / "manifest.json").exists()
        assert "traps" in capsys.readouterr().err

    def test_check_reports_and_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = TestCheck.manifest()
        manifest["courses"][0]["enrol"]["ghost"] = "student"
        (tmp_path / "files").mkdir()
        (tmp_path / "files" / "plan.pdf").write_bytes(b"%PDF")
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        assert bm.main(["--check", str(tmp_path)]) == 1
        assert "unknown user ghost" in capsys.readouterr().out

        del manifest["courses"][0]["enrol"]["ghost"]
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        assert bm.main(["--check", str(tmp_path)]) == 0


def test_the_example_config_documents_exactly_the_defaults() -> None:
    example = (ROOT / "dev" / "seed" / "seed.example.toml").read_text()

    assert bm.parse_config(example) == bm.Config()
