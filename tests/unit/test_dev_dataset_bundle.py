"""dev/seed/dataset_bundle.py: a dataset as one portable file, re-importable anywhere.

A bundle carries everything a checkout needs to seed the dataset again, and to rebuild
its manifest with other seed.toml rules, without the server it came from: the manifest,
the legacy index, the backups and plain files, and only the blobs anything refers to.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from support.dev_seed import Legacy, bm, file_url, load_seed_script

db = load_seed_script("dataset_bundle")


@pytest.fixture
def dataset(tmp_path: Path) -> Legacy:
    """A small built dataset: a page, a resource, a backup, plus junk a bundle must drop."""
    legacy = Legacy(tmp_path / "dataset")
    legacy.doc("7:10:0", modname="page", kind="html", blob_sha256=legacy.blob(b"<p>Seite</p>"))
    legacy.doc(
        "7:11:0",
        modname="resource",
        kind="file",
        filename="a.pdf",
        file_url=file_url(9, "mod_resource", "a.pdf"),
        blob_sha256=legacy.blob(b"%PDF"),
    )
    # A url module's fetched page: in the legacy blobs, but nothing seeds it.
    legacy.doc(
        "7:12:0",
        modname="url",
        kind="external",
        external_url="https://x.invalid/",
        blob_sha256=legacy.blob(b"<html>fetched</html>"),
    )
    # Deleted in Moodle: its blob is dead weight.
    legacy.doc(
        "7:13:0",
        modname="resource",
        kind="file",
        filename="old.pdf",
        tombstoned_at=1,
        file_url=file_url(9, "mod_resource", "old.pdf"),
        blob_sha256=legacy.blob(b"old"),
    )
    legacy.blob(b"orphan")
    legacy.backup("kurs.mbz", b"backup")
    (legacy.root / "seed.toml").write_text('prefix = "legacy"\n')
    (legacy.root / ".DS_Store").write_bytes(b"junk")
    legacy.db.close()
    assert bm.main([str(legacy.root)]) == 0
    return legacy


def members(bundle: Path) -> set[str]:
    with tarfile.open(bundle) as tar:
        return {m.name for m in tar.getmembers() if m.isfile()}


def blob(data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    return f"blobs/{sha[:2]}/{sha}"


class TestPack:
    def test_keeps_what_seeding_and_rebuilding_need_and_nothing_else(
        self, dataset: Legacy, tmp_path: Path
    ) -> None:
        bundle = db.pack(dataset.root, tmp_path / "out.tar.gz")

        assert members(bundle) == {
            "BUNDLE.json",
            "manifest.json",
            "seed.toml",
            "documents.db",
            "backups/kurs.mbz",
            blob(b"<p>Seite</p>"),  # rebuilding the page needs its HTML
            blob(b"%PDF"),
        }

    def test_bundle_json_describes_the_dataset(self, dataset: Legacy, tmp_path: Path) -> None:
        bundle = db.pack(dataset.root, tmp_path / "out.tar.gz")

        with tarfile.open(bundle) as tar:
            extracted = tar.extractfile("BUNDLE.json")
            assert extracted is not None
            info = json.load(extracted)
        assert info["bundle_version"] == 1
        assert info["prefix"] == "legacy"
        assert (info["courses"], info["modules"], info["backups"], info["blobs"]) == (1, 3, 1, 2)
        assert info["created"]

    def test_plain_files_of_a_hand_written_manifest_are_kept(self, tmp_path: Path) -> None:
        root = tmp_path / "hand"
        (root / "files").mkdir(parents=True)
        (root / "files" / "plan.pdf").write_bytes(b"%PDF")
        manifest = {
            "version": 2,
            "prefix": "hand",
            "category": "Hand",
            "users": [],
            "courses": [
                {
                    "idnumber": "hand:1",
                    "fullname": "K",
                    "shortname": "k",
                    "enrol": {},
                    "sections": [
                        {
                            "name": "A",
                            "modules": [
                                {
                                    "idnumber": "hand:1:a",
                                    "modname": "resource",
                                    "name": "Plan",
                                    "files": [
                                        {
                                            "filepath": "/",
                                            "filename": "plan.pdf",
                                            "path": "files/plan.pdf",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            "backups": [],
            "restore_users": False,
            "skipped": [],
        }
        (root / "manifest.json").write_text(json.dumps(manifest))

        assert members(db.pack(root, tmp_path / "h.tar.gz")) == {
            "BUNDLE.json",
            "manifest.json",
            "files/plan.pdf",
        }

    def test_an_unbuilt_or_broken_dataset_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(db.BundleError, match=r"manifest\.json"):
            db.pack(tmp_path, tmp_path / "x.tar.gz")

        (tmp_path / "manifest.json").write_text(json.dumps({"version": 1}))
        with pytest.raises(db.BundleError, match="version"):
            db.pack(tmp_path, tmp_path / "x.tar.gz")
        assert not (tmp_path / "x.tar.gz").exists()


class TestUnpack:
    def test_round_trip_is_self_contained(self, dataset: Legacy, tmp_path: Path) -> None:
        bundle = db.pack(dataset.root, tmp_path / "out.tar.gz")
        target = tmp_path / "elsewhere" / "dataset"

        db.unpack(bundle, target)

        original = json.loads((dataset.root / "manifest.json").read_text())
        assert json.loads((target / "manifest.json").read_text()) == original
        # Rebuilding from the bundle alone gives the same manifest.
        (target / "manifest.json").unlink()
        assert bm.main([str(target)]) == 0
        assert json.loads((target / "manifest.json").read_text()) == original
        assert bm.check_manifest(original, target) == []

    def test_refuses_to_overwrite_a_dataset_unless_forced(
        self, dataset: Legacy, tmp_path: Path
    ) -> None:
        bundle = db.pack(dataset.root, tmp_path / "out.tar.gz")

        with pytest.raises(db.BundleError, match="already holds a dataset"):
            db.unpack(bundle, dataset.root)
        db.unpack(bundle, dataset.root, force=True)

    def test_a_corrupted_blob_is_detected(self, dataset: Legacy, tmp_path: Path) -> None:
        good = db.pack(dataset.root, tmp_path / "good.tar.gz")
        bad = tmp_path / "bad.tar.gz"
        with tarfile.open(good) as src, tarfile.open(bad, "w:gz") as dst:
            for member in src.getmembers():
                data = src.extractfile(member)
                if member.name == blob(b"%PDF"):
                    member.size = 4
                    data = __import__("io").BytesIO(b"EVIL")
                dst.addfile(member, data)

        with pytest.raises(db.BundleError, match="corrupt"):
            db.unpack(bad, tmp_path / "t")

    def test_members_escaping_the_target_are_refused(self, tmp_path: Path) -> None:
        evil = tmp_path / "evil.tar.gz"
        (tmp_path / "x").write_text("x")
        with tarfile.open(evil, "w:gz") as tar:
            tar.add(tmp_path / "x", arcname="../escaped")

        with pytest.raises(db.BundleError):
            db.unpack(evil, tmp_path / "t")
        assert not (tmp_path / "escaped").exists()


def test_cli(dataset: Legacy, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "b.tar.gz"

    assert db.main(["pack", str(dataset.root), str(bundle)]) == 0
    assert db.main(["unpack", str(bundle), str(tmp_path / "t")]) == 0
    assert db.main(["unpack", str(bundle), str(tmp_path / "t")]) == 2
    out, err = capsys.readouterr()
    assert "1 courses, 3 modules, 1 backups" in out
    assert "already holds a dataset" in err
