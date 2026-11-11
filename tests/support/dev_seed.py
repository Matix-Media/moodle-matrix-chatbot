"""The dev seeding scripts (dev/seed/) for tests, and a legacy dataset double."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SITE = "https://moodle.example.invalid"


def load_seed_script(name: str) -> ModuleType:
    """Import dev/seed/<name>.py once (it is a script, not a package)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "dev" / "seed" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


bm = load_seed_script("build_manifest")


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


def file_url(context: int, component: str, path: str) -> str:
    return (
        f"{SITE}/webservice/pluginfile.php/{context}/{component}/content/3/{path}?forcedownload=1"
    )
