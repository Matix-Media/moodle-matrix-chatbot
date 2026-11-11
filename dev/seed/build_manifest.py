"""Build the seed manifest the dev Moodle recreates (see dev/seed/README.md).

    python dev/seed/build_manifest.py dev/dataset [--config seed.toml]
    python dev/seed/build_manifest.py --check dev/dataset   # validate a manifest.json

Sources in the dataset directory, both optional:

* ``documents.db`` + ``blobs/<aa>/<sha256>`` — a legacy (Matrix-era bsbot) content index
  (see fetch-legacy.sh): one row per document (a folder has a row per file, a book a
  row per chapter, an intro its own row), regrouped here into courses → sections →
  modules in the order the legacy crawler walked them.
* ``backups/*.mbz`` — Moodle course backups, restored as they are by the seeder.

``seed.toml`` (next to the data, or ``--config``) sets the id prefix, category, course
subset, file-size cap, access traps and the users with their enrolment patterns; see
``Config``. Anything that cannot be recreated is listed under ``skipped``.

The legacy index does not record visibility or enrolments, so both follow the config's
rules: every test user gets courses, modules and sections they can and cannot see.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

Json = dict[str, Any]

MANIFEST_VERSION = 2
# Module types the seed script knows how to create.
SUPPORTED = {"page", "book", "folder", "resource", "url", "label"} | {
    "assign",
    "quiz",
    "forum",
    "choice",
    "feedback",
}
# Modules that are nothing without their content.
NEEDS_CONTENT = {"resource": "files", "folder": "files", "book": "chapters", "url": "externalurl"}

_REF = re.compile(r"""(?P<attr>\b(?:src|href)\s*=\s*)(?P<q>["'])(?P<url>[^"']*)(?P=q)""", re.I)


def text_to_html(text: str) -> str:
    """Extracted plain text back to simple HTML: blank lines split paragraphs."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "\n".join(
        "<p>" + "<br>\n".join(html.escape(line, quote=False) for line in p.split("\n")) + "</p>"
        for p in paragraphs
    )


def embed_files(content: str, filenames: set[str]) -> str:
    """Point relative references to the module's own files at ``@@PLUGINFILE@@``.

    Moodle stores embedded files as ``@@PLUGINFILE@@/name``; the legacy HTML kept just
    ``name`` (the prefix stripped), which only resolves once the file is in the area.
    """

    def swap(m: re.Match[str]) -> str:
        url = m["url"]
        if unquote(url.split("?")[0]) not in filenames:
            return m[0]
        return f"{m['attr']}{m['q']}@@PLUGINFILE@@/{url}{m['q']}"

    return _REF.sub(swap, content)


def file_location(url: str, component: str) -> tuple[str, str] | None:
    """``(filepath, filename)`` of a pluginfile URL inside ``component``'s content area."""
    path = unquote(urlsplit(url).path)
    marker = f"/{component}/content/"
    if marker not in path:
        return None
    rest = path.split(marker, 1)[1].split("/", 1)  # drop the revision number
    if len(rest) != 2 or not rest[1]:
        return None
    *dirs, name = rest[1].split("/")
    return ("/" + "".join(f"{d}/" for d in dirs if d), name)


class ConfigError(ValueError):
    """seed.toml (or the dataset) is unusable; the message says why."""


@dataclass(frozen=True)
class User:
    """A test user, created by the seeder if missing (password ``Test1234!``)."""

    username: str
    role: str
    # Repeats over the courses in order: "1" enrolled, "0" not.
    pattern: str = "1"
    firstname: str = ""

    def enrolled(self, index: int) -> bool:
        return self.pattern[index % len(self.pattern)] == "1"


DEFAULT_USERS = (
    User("teacher", "editingteacher", "1", "Tina"),
    User("student", "student", "1110", "Sophie"),
    User("student2", "student", "10", "Bruno"),
)
TRAP_MODES = ("alternate", "hidden", "restricted", "none")
_PREFIX = re.compile(r"[a-z0-9][a-z0-9_-]{0,23}")


@dataclass(frozen=True)
class Config:
    """seed.toml. Every key is optional; unknown keys are errors (typos must not pass)."""

    # Of every idnumber ("<prefix>:2250") and the category: datasets with different
    # prefixes live side by side in one Moodle.
    prefix: str = "legacy"
    category: str = ""
    # Legacy course ids to seed; empty = all.
    courses: tuple[int, ...] = ()
    # Larger files are left out (skipped "too large"); 0 = no limit.
    max_file_mb: float = 0
    # What each generated course hides from students (``add_access_trap``).
    traps: str = "alternate"
    # Restore the user data (accounts, enrolments, posts) inside .mbz backups.
    restore_users: bool = False
    users: tuple[User, ...] = field(default=DEFAULT_USERS)

    @property
    def category_name(self) -> str:
        if self.category:
            return self.category
        return "Legacy dataset" if self.prefix == "legacy" else f"{self.prefix} dataset"


def _user(name: str, raw: Any) -> User:
    if not isinstance(raw, dict):
        raise ConfigError(f"users.{name} must be a table")
    unknown = set(raw) - {"role", "pattern", "firstname"}
    if unknown:
        raise ConfigError(f"users.{name}: unknown keys {sorted(unknown)}")
    role, pattern = raw.get("role"), raw.get("pattern", "1")
    if not isinstance(role, str) or not role:
        raise ConfigError(f"users.{name}.role is required (e.g. student, editingteacher)")
    if not isinstance(pattern, str) or not re.fullmatch(r"[01]+", pattern):
        raise ConfigError(f'users.{name}.pattern must be 0s and 1s, e.g. "1110"')
    firstname = raw.get("firstname") or name.capitalize()
    return User(name, role, pattern, str(firstname))


def parse_config(text: str) -> Config:
    """Parse seed.toml; raises ConfigError on anything unexpected."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"seed.toml: {exc}") from exc
    known = set(Config.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown keys {sorted(unknown)}; known: {sorted(known)}")
    prefix = raw.get("prefix", "legacy")
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ConfigError("prefix must be 1-24 lower-case letters, digits, - or _")
    courses = raw.get("courses", [])
    if not isinstance(courses, list) or not all(isinstance(c, int) for c in courses):
        raise ConfigError("courses must be a list of legacy course ids")
    cap = raw.get("max_file_mb", 0)
    if not isinstance(cap, int | float) or cap < 0:
        raise ConfigError("max_file_mb must be a number >= 0")
    traps = raw.get("traps", "alternate")
    if traps not in TRAP_MODES:
        raise ConfigError(f"traps must be one of {', '.join(TRAP_MODES)}")
    restore_users = raw.get("restore_users", False)
    if not isinstance(restore_users, bool):
        raise ConfigError("restore_users must be true or false")
    users = DEFAULT_USERS
    if "users" in raw:
        if not isinstance(raw["users"], dict) or not raw["users"]:
            raise ConfigError("users needs at least one [users.<name>] table")
        users = tuple(_user(name, u) for name, u in raw["users"].items())
    return Config(
        prefix=prefix,
        category=str(raw.get("category", "")),
        courses=tuple(courses),
        max_file_mb=float(cap),
        traps=traps,
        restore_users=restore_users,
        users=users,
    )


def enrolments(users: tuple[User, ...], index: int) -> dict[str, str]:
    """Who is enrolled in the index-th course (generated and backups counted together)."""
    return {u.username: u.role for u in users if u.enrolled(index)}


# 2099-01-01 UTC: a section restricted "from" this date stays closed to students.
RESTRICTED_FROM = 4_070_908_800
# Modules worth hiding: real content a leak would expose, not a label or a link.
CONTENT = ("page", "book", "resource", "folder")


def add_access_trap(sections: list[Json], index: int, mode: str = "alternate") -> None:
    """Hide one thing from students: a module, or a whole section.

    Teachers keep seeing both (viewhiddenactivities, ignoreavailabilityrestrictions),
    so the dataset exercises the plugin's per-user scope in every course. The legacy
    index records no visibility, so these are chosen, not real: the last content module,
    or the last section (never the general one), shown greyed out with a date that
    never arrives. ``alternate`` restricts a section in every other course; a course
    with a single section always gets a hidden module instead.
    """
    if mode == "none":
        return
    wants_section = mode == "restricted" or (mode == "alternate" and index % 2 == 1)
    if wants_section and len(sections) > 1:
        sections[-1]["available_from"] = RESTRICTED_FROM
        return
    modules = [m for s in sections for m in s["modules"]]
    candidates = [m for m in modules if m["modname"] in CONTENT] or modules
    candidates[-1]["visible"] = False


def _clean(name: str | None) -> str:
    return html.unescape(name or "").strip()[:255]


class _Builder:
    def __init__(self, root: Path, config: Config) -> None:
        self.root = root
        self.config = config
        self.skipped: list[Json] = []

    def blob_path(self, sha: str) -> Path:
        return self.root / "blobs" / sha[:2] / sha

    def skip(self, doc: Json, reason: str) -> None:
        self.skipped.append(
            {
                "course": doc["course_id"],
                "cmid": doc["module_id"],
                "doc_id": doc["doc_id"],
                "name": _clean(doc["filename"] or doc["module_name"]),
                "reason": reason,
            }
        )

    def blob(self, doc: Json) -> str | None:
        sha = doc["blob_sha256"]
        if not sha:
            self.skip(doc, "no blob")
            return None
        path = self.blob_path(sha)
        if not path.is_file():
            self.skip(doc, "blob missing")
            return None
        cap = self.config.max_file_mb
        if cap and doc["kind"] == "file" and path.stat().st_size > cap * 1_000_000:
            self.skip(doc, "too large")
            return None
        return str(sha)

    def module(self, docs: list[Json]) -> Json | None:
        first = docs[0]
        modname = first["modname"]
        if modname not in SUPPORTED:
            self.skip(first, f"unsupported modname {modname}")
            return None
        module: Json = {
            "idnumber": f"{self.config.prefix}:{first['module_id']}",
            "legacy_cmid": first["module_id"],
            "modname": modname,
            "name": _clean(first["module_name"]) or modname,
        }
        component = f"mod_{modname}"
        files: list[Json] = []
        chapters: list[Json] = []
        intro: list[str] = []
        for doc in docs:
            kind = doc["kind"]
            if kind == "inline":
                if (doc["text"] or "").strip():
                    intro.append(text_to_html(doc["text"]))
            elif kind == "external":
                module["externalurl"] = doc["external_url"]
            elif kind == "html":
                sha = self.blob(doc)
                if sha is None:
                    continue
                content = self.blob_path(sha).read_bytes().decode("utf-8", "replace")
                if modname == "book":
                    chapters.append(
                        {"title": _clean(doc["title"]) or "Kapitel", "content": content}
                    )
                else:
                    module["content"] = content
            elif kind == "file":
                where = file_location(doc["file_url"] or "", component)
                if where is None:
                    where = ("/", doc["filename"] or "file")
                sha = self.blob(doc)
                if sha is not None:
                    files.append({"filepath": where[0], "filename": where[1], "blob": sha})
        if intro:
            module["intro"] = "\n".join(intro)
        if files:
            module["files"] = files
        if chapters:
            module["chapters"] = chapters
        if modname == "page" and "content" in module and files:
            module["content"] = embed_files(module["content"], {f["filename"] for f in files})
        if modname == "page" and "content" not in module:
            module["content"] = module.get("intro", "")
        needs = NEEDS_CONTENT.get(modname)
        if not (needs in module if needs else module.get("intro") or module.get("content")):
            self.skip(first, "no content")
            return None
        return module


def _load(root: Path) -> list[Json]:
    db = sqlite3.connect(f"file:{root / 'documents.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM documents WHERE tombstoned_at IS NULL AND course_id > 0 ORDER BY rowid"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def _backups(root: Path, config: Config, first_index: int) -> list[Json]:
    out = []
    for i, path in enumerate(sorted((root / "backups").glob("*.mbz"))):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        out.append(
            {
                "file": path.relative_to(root).as_posix(),
                "idnumber": f"{config.prefix}:mbz:{sha[:16]}",
                "enrol": enrolments(config.users, first_index + i),
            }
        )
    return out


def build_manifest(root: Path, config: Config | None = None) -> Json:
    """The manifest for the dataset in ``root``."""
    config = config or Config()
    has_index = (root / "documents.db").is_file()
    if not has_index and not any((root / "backups").glob("*.mbz")):
        raise ConfigError(f"nothing to seed in {root}: no documents.db, no backups/*.mbz")
    builder = _Builder(root, config)
    # dicts keep insertion (= first-seen) order at every level.
    courses: dict[int, Json] = {}
    for doc in _load(root) if has_index else []:
        if config.courses and doc["course_id"] not in config.courses:
            continue
        course = courses.setdefault(
            doc["course_id"], {"name": _clean(doc["course_name"]), "sections": {}}
        )
        section = course["sections"].setdefault(doc["section_name"] or "", {})
        section.setdefault(doc["module_id"], []).append(doc)

    out: list[Json] = []
    for legacy_id in sorted(courses):
        course = courses[legacy_id]
        sections = []
        for name, modules in course["sections"].items():
            built = [m for docs in modules.values() if (m := builder.module(docs))]
            if built:
                sections.append({"name": _clean(name), "modules": built})
        if sections:
            add_access_trap(sections, len(out), config.traps)
            out.append(
                {
                    "idnumber": f"{config.prefix}:{legacy_id}",
                    "legacy_id": legacy_id,
                    "fullname": course["name"],
                    "shortname": f"{config.prefix}-{legacy_id}",
                    "sections": sections,
                    "enrol": enrolments(config.users, len(out)),
                }
            )
    return {
        "version": MANIFEST_VERSION,
        "prefix": config.prefix,
        "category": config.category_name,
        "users": [{"username": u.username, "firstname": u.firstname} for u in config.users],
        "courses": out,
        "backups": _backups(root, config, len(out)),
        "restore_users": config.restore_users,
        "skipped": builder.skipped,
    }


def _within(root: Path, relative: str) -> Path | None:
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def check_manifest(manifest: Json, root: Path) -> list[str]:
    """Problems that would make the seeder fail or seed something else than meant."""
    errors: list[str] = []
    if manifest.get("version") != MANIFEST_VERSION:
        errors.append(f"version must be {MANIFEST_VERSION}")
    for key in ("prefix", "category"):
        if not isinstance(manifest.get(key), str) or not manifest.get(key):
            errors.append(f"{key} is required")
    users = {u.get("username") for u in manifest.get("users", [])}
    # Courses (generated and restored) and modules are separate Moodle tables.
    seen: dict[str, set[str]] = {"course": set(), "module": set()}

    def idnumber(where: str, value: Any, table: str = "course") -> None:
        if not isinstance(value, str) or not value:
            errors.append(f"{where}: idnumber is required")
        elif value in seen[table]:
            errors.append(f"{where}: duplicate idnumber {value}")
        else:
            seen[table].add(value)

    def enrol(where: str, value: Any) -> None:
        for username in value or {}:
            if username not in users:
                errors.append(f"{where}: unknown user {username} (declare it under users)")

    def file(where: str, relative: Any) -> None:
        path = _within(root, str(relative))
        if path is None:
            errors.append(f"{where}: {relative} is outside the dataset")
        elif not path.is_file():
            errors.append(f"{where}: missing file {relative}")

    for c in manifest.get("courses", []):
        cwhere = f"course {c.get('idnumber')}"
        idnumber(cwhere, c.get("idnumber"))
        for key in ("fullname", "shortname"):
            if not c.get(key):
                errors.append(f"{cwhere}: {key} is required")
        enrol(cwhere, c.get("enrol"))
        if not c.get("sections"):
            errors.append(f"{cwhere}: needs at least one section")
        for s in c.get("sections", []):
            for m in s.get("modules", []):
                mwhere = f"{cwhere} module {m.get('idnumber')}"
                idnumber(mwhere, m.get("idnumber"), "module")
                modname = m.get("modname")
                if modname not in SUPPORTED:
                    errors.append(f"{mwhere}: unsupported modname {modname}")
                    continue
                needs = NEEDS_CONTENT.get(modname)
                if needs and not m.get(needs):
                    errors.append(f"{mwhere}: {modname} needs {needs}")
                for f in m.get("files", []):
                    if not f.get("filename") or not str(f.get("filepath", "")).startswith("/"):
                        errors.append(f"{mwhere}: files need filename and a /filepath/")
                    if "path" in f:
                        file(mwhere, f["path"])
                    elif "blob" in f:
                        file(mwhere, f"blobs/{f['blob'][:2]}/{f['blob']}")
                    else:
                        errors.append(f"{mwhere}: {f.get('filename')} needs blob or path")
    for b in manifest.get("backups", []):
        bwhere = f"backup {b.get('file')}"
        idnumber(bwhere, b.get("idnumber"))
        enrol(bwhere, b.get("enrol"))
        file(bwhere, b.get("file", ""))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("dataset", type=Path, help="dataset directory")
    parser.add_argument("--config", type=Path, help="seed.toml (default: <dataset>/seed.toml)")
    parser.add_argument("--check", action="store_true", help="only validate manifest.json")
    args = parser.parse_args(argv)
    root: Path = args.dataset
    target = root / "manifest.json"

    if args.check:
        errors = check_manifest(json.loads(target.read_text()), root)
        for error in errors:
            print(error)
        print(f"{target}: {'ok' if not errors else f'{len(errors)} problems'}")
        return 1 if errors else 0

    config_path: Path | None = args.config or (root / "seed.toml")
    if not config_path.is_file():
        if args.config:
            print(f"no such config: {config_path}", file=sys.stderr)
            return 2
        config_path = None
    try:
        config = parse_config(config_path.read_text()) if config_path else Config()
        manifest = build_manifest(root, config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    modules = sum(len(s["modules"]) for c in manifest["courses"] for s in c["sections"])
    files = sum(
        len(m.get("files", []))
        for c in manifest["courses"]
        for s in c["sections"]
        for m in s["modules"]
    )
    print(
        f"{len(manifest['courses'])} courses, {modules} modules, {files} files, "
        f"{len(manifest['backups'])} backups; {len(manifest['skipped'])} skipped → {target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
