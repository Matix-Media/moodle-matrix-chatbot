"""Generate the synthetic "demo" dataset: a whole fictional class, ready to seed.

Six logically connected courses of one vocational-school class (FIAE 25a, school year
2026/27), all built around one shared project for a fictional logistics company. Every
file format and external link type the app handles appears at least once, plus the
traps a real course has (hidden modules, restricted sections, mislabelled and
unsupported files, scans that need OCR). Everything is invented, so unlike the legacy
bundle the output may be shared freely.

    python dev/seed/generate_demo.py dev/datasets/demo
    MLC_DATASET_DIR=./datasets/demo docker compose -f dev/docker-compose.yml up -d --wait

Writes ``manifest.json``, ``files/…`` and ``PROBES.md`` (questions to try, with the
expected answer and who may see it). Needs the ``[dev]`` extra (pymupdf, python-docx,
python-pptx, openpyxl, xlwt, Pillow).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_manifest import check_manifest
from demo.courses import englisch, klassenraum, lf3, lf5, lf8, wiso
from demo.model import Dataset

USERS = [
    {"username": "teacher", "firstname": "Tina"},
    {"username": "student", "firstname": "Sophie"},
    {"username": "student2", "firstname": "Bruno"},
]

ENROLMENT = """
| Kurs | teacher | student (Sophie) | student2 (Bruno) |
|---|---|---|---|
| Klassenraum | ✓ | ✓ | ✓ |
| LF5 Datenbanken | ✓ | ✓ | – |
| LF3 Netzwerke | ✓ | ✓ | ✓ |
| WiSo | ✓ | ✓ | – |
| Englisch | ✓ | ✓ | – |
| LF8 Schnittstellen | ✓ | – | ✓ |
"""


def build(root: Path) -> Dataset:
    ds = Dataset(root=root, prefix="demo", category="Demo dataset (BS Elbmarsch)", users=USERS)
    for course in (klassenraum, lf5, lf3, wiso, englisch, lf8):
        course.build(ds)
    return ds


def probes_markdown(ds: Dataset) -> str:
    lines = [
        "# Demo dataset – questions to try",
        "",
        "Logins: teacher / student / student2, password `Test1234!`.",
        "",
        "## Enrolments",
        ENROLMENT,
        "## Questions",
        "",
        "| Frage | Erwartete Antwort | Quelle | Wer |",
        "|---|---|---|---|",
    ]
    lines += [f"| {p.question} | {p.answer} | {p.source} | {p.users} |" for p in ds.probes]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("dataset", type=Path, nargs="?", default=Path("dev/datasets/demo"))
    parser.add_argument("--force", action="store_true", help="replace an existing dataset")
    args = parser.parse_args(argv)
    root: Path = args.dataset
    if root.exists() and any(root.iterdir()):
        if not args.force:
            print(f"{root} is not empty; pass --force to replace it", file=sys.stderr)
            return 2
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    ds = build(root)
    manifest = ds.manifest()
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (root / "PROBES.md").write_text(probes_markdown(ds))

    errors = check_manifest(manifest, root)
    for error in errors:
        print(error, file=sys.stderr)
    modules = [m for c in manifest["courses"] for s in c["sections"] for m in s["modules"]]
    files = [f for m in modules for f in m.get("files", [])]
    kinds = Counter(Path(f["filename"]).suffix.lower() or "(none)" for f in files)
    print(
        f"{len(manifest['courses'])} courses, {len(modules)} modules, {len(files)} files, "
        f"{len(ds.probes)} probes → {root}"
    )
    print(
        "modules: "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(m["modname"] for m in modules).items()))
    )
    print("files:   " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
