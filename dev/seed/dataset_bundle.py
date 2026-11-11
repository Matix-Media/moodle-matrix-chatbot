"""Pack a dataset into one portable file, and unpack it into any checkout.

    python dev/seed/dataset_bundle.py pack   dev/dataset  ~/…/legacy.tar.gz
    python dev/seed/dataset_bundle.py unpack ~/…/legacy.tar.gz  dev/dataset [--force]

A bundle carries everything needed to seed the dataset again, and to rebuild its
manifest with other seed.toml rules, without the server it came from: manifest.json,
seed.toml, documents.db, backups/ and plain files the manifest names, and only the
blobs something refers to (not those of deleted documents or fetched link targets).
``BUNDLE.json`` describes it. Unpacking refuses members outside the target, verifies
every blob against its sha256 name, and only then moves the dataset into place.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any

BUNDLE_VERSION = 1


class BundleError(RuntimeError):
    """The dataset or bundle is unusable; the message says why."""


def _build_manifest() -> ModuleType:
    if "build_manifest" in sys.modules:
        return sys.modules["build_manifest"]
    path = Path(__file__).with_name("build_manifest.py")
    spec = importlib.util.spec_from_file_location("build_manifest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_manifest"] = module
    spec.loader.exec_module(module)
    return module


def _blob(sha: str) -> str:
    return f"blobs/{sha[:2]}/{sha}"


def _index_blobs(root: Path) -> set[str]:
    """Blobs a rebuild from documents.db reads: live HTML and file documents."""
    if not (root / "documents.db").is_file():
        return set()
    db = sqlite3.connect(f"file:{root / 'documents.db'}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT DISTINCT blob_sha256 FROM documents WHERE tombstoned_at IS NULL"
            " AND course_id > 0 AND kind IN ('html', 'file') AND blob_sha256 IS NOT NULL"
        ).fetchall()
    finally:
        db.close()
    return {_blob(sha) for (sha,) in rows if (root / _blob(sha)).is_file()}


def _members(root: Path, manifest: dict[str, Any]) -> list[str]:
    paths = {"manifest.json"} | {p for p in ("seed.toml", "documents.db") if (root / p).is_file()}
    paths |= {b["file"] for b in manifest.get("backups", [])}
    for course in manifest["courses"]:
        for section in course["sections"]:
            for module in section["modules"]:
                for f in module.get("files", []):
                    paths.add(f["path"] if "path" in f else _blob(f["blob"]))
    paths |= _index_blobs(root)
    return sorted(paths)


def _summary(info: dict[str, Any]) -> str:
    return (
        f"{info['courses']} courses, {info['modules']} modules, {info['backups']} backups, "
        f"{info['blobs']} blobs ({info['bytes'] / 1e6:.0f} MB)"
    )


def pack(root: Path, out: Path) -> Path:
    """Write the dataset in ``root`` (manifest built and valid) to the bundle ``out``."""
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BundleError(f"no manifest.json in {root}: build it first (build_manifest.py)")
    manifest = json.loads(manifest_path.read_text())
    errors = _build_manifest().check_manifest(manifest, root)
    if errors:
        raise BundleError(f"{manifest_path} is not valid: " + "; ".join(errors[:5]))

    members = _members(root, manifest)
    info = {
        "bundle_version": BUNDLE_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prefix": manifest["prefix"],
        "courses": len(manifest["courses"]),
        "modules": sum(len(s["modules"]) for c in manifest["courses"] for s in c["sections"]),
        "backups": len(manifest.get("backups", [])),
        "blobs": sum(m.startswith("blobs/") for m in members),
        "bytes": sum((root / m).stat().st_size for m in members),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    # PDFs and Office files are compressed already; level 1 keeps packing fast.
    with tarfile.open(partial, "w:gz", compresslevel=1) as tar:
        data = json.dumps(info, indent=1).encode()
        entry = tarfile.TarInfo("BUNDLE.json")
        entry.size, entry.mtime = len(data), int(time.time())
        tar.addfile(entry, io.BytesIO(data))
        for member in members:
            tar.add(root / member, arcname=member, recursive=False)
    partial.replace(out)
    return out


def _verify(root: Path) -> dict[str, Any]:
    if not (root / "BUNDLE.json").is_file() or not (root / "manifest.json").is_file():
        raise BundleError("not a dataset bundle: no BUNDLE.json or manifest.json")
    info: dict[str, Any] = json.loads((root / "BUNDLE.json").read_text())
    if info.get("bundle_version") != BUNDLE_VERSION:
        raise BundleError(f"bundle version {info.get('bundle_version')}, expected {BUNDLE_VERSION}")
    for path in (root / "blobs").glob("*/*"):
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != path.name:
            raise BundleError(f"corrupt bundle: {path.relative_to(root)} does not match its hash")
    return info


def unpack(bundle: Path, target: Path, *, force: bool = False) -> dict[str, Any]:
    """Unpack ``bundle`` into ``target``, replacing a dataset there only with ``force``."""
    if target.exists() and any(target.iterdir()):
        if not (target / "manifest.json").exists():
            raise BundleError(f"{target} is not empty and not a dataset; pick another target")
        if not force:
            raise BundleError(f"{target} already holds a dataset; --force replaces it")
    staging = target.with_name(f".{target.name}.unpacking")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(bundle) as tar:
            tar.extractall(staging, filter="data")
        info = _verify(staging)
    except (tarfile.TarError, OSError, BundleError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise exc if isinstance(exc, BundleError) else BundleError(f"{bundle}: {exc}") from exc
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack", help="dataset directory → bundle")
    p.add_argument("dataset", type=Path)
    p.add_argument("bundle", type=Path)
    u = sub.add_parser("unpack", help="bundle → dataset directory")
    u.add_argument("bundle", type=Path)
    u.add_argument("dataset", type=Path)
    u.add_argument("--force", action="store_true", help="replace a dataset already there")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            out = pack(args.dataset, args.bundle)
            with tarfile.open(out) as tar:
                extracted = tar.extractfile("BUNDLE.json")
                assert extracted is not None
                info = json.load(extracted)
            print(f"{_summary(info)} → {out} ({out.stat().st_size / 1e6:.0f} MB)")
        else:
            info = unpack(args.bundle, args.dataset, force=args.force)
            print(f"{_summary(info)}, created {info['created']} → {args.dataset}")
            print("seed it: docker compose -f dev/docker-compose.yml restart moodle")
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
