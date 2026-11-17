"""dev/seed/generate_demo.py: a synthetic dataset that exercises everything the app ingests.

The demo dataset is only useful if it keeps covering every file format, external link
adapter and access trap the app handles, and if what the plugin uploads actually
extracts. These tests pin that, so a new extractor or adapter shows up here as a gap.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from moodle_llm_chat.ingest import extract as ex
from moodle_llm_chat.ingest.google_docs import GoogleDocsUnsupported, google_export_url
from moodle_llm_chat.ingest.hackmd import is_hackmd_note
from moodle_llm_chat.ingest.nextcloud import is_nextcloud_share
from moodle_llm_chat.ingest.taskcards import is_taskcards_url
from moodle_llm_chat.ingest.youtube import extract_video_id
from support.dev_seed import ROOT, bm, load_seed_script

gen = load_seed_script("generate_demo")

#: local_llmchat's default file type allowlist, read from the plugin so the two stay in step.
PLUGIN_FILETYPES = set(
    re.search(
        r"DEFAULT_FILETYPES = '([^']+)'",
        (ROOT / "moodle/local_llmchat/classes/content/document_builder.php").read_text(),
    )
    .group(1)
    .split(",")
)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    assert gen.main([str(root), "--force"]) == 0
    return root


@pytest.fixture(scope="module")
def manifest(dataset: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((dataset / "manifest.json").read_text())
    return data


def modules(manifest: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for course in manifest["courses"]:
        for section in course["sections"]:
            yield from section["modules"]


def files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for m in modules(manifest) for f in m.get("files", [])]


def test_manifest_is_valid_for_the_seeder(dataset: Path, manifest: dict[str, Any]) -> None:
    assert bm.check_manifest(manifest, dataset) == []


def test_refuses_to_overwrite_without_force(dataset: Path) -> None:
    assert gen.main([str(dataset)]) == 2


def test_every_seedable_module_type_is_used(manifest: dict[str, Any]) -> None:
    assert {m["modname"] for m in modules(manifest)} == bm.SUPPORTED


def test_every_extractor_has_a_file(manifest: dict[str, Any]) -> None:
    extensions = {Path(f["filename"]).suffix.lower().lstrip(".") for f in files(manifest)}
    assert set(ex._BY_EXTENSION) - extensions == set()


def test_every_external_adapter_has_a_link(manifest: dict[str, Any]) -> None:
    urls = [m["externalurl"] for m in modules(manifest) if m["modname"] == "url"]

    def google(kind: str) -> bool:
        for url in urls:
            try:
                if google_export_url(url)[1] == kind:
                    return True
            except GoogleDocsUnsupported:
                continue
        return False

    assert any(extract_video_id(u) for u in urls)
    assert any(is_taskcards_url(u) for u in urls)
    assert any(is_hackmd_note(u) for u in urls)
    assert any(is_nextcloud_share(u) for u in urls)
    assert google("text") and google("pptx") and google("xlsx")


def test_external_links_point_at_the_published_documents(manifest: dict[str, Any]) -> None:
    # The demo's TaskCards, HackMD, Google, Nextcloud and YouTube links are real and live
    # (dev/seed/check_demo_links.py fetches them); none may fall back to an invented host.
    from demo.external import URLS

    urls = {m["externalurl"] for m in modules(manifest) if m["modname"] == "url"}
    assert set(URLS.values()) <= urls
    assert not [u for u in urls if ".example" in u]


def test_files_the_plugin_uploads_extract_or_fail_as_intended(
    dataset: Path, manifest: dict[str, Any]
) -> None:
    # Allowed by the plugin but without an API extractor: realistic noise that must fail
    # cleanly rather than produce garbage.
    unsupported = {"rtf", "json", "xml"}
    for f in files(manifest):
        extension = Path(f["filename"]).suffix.lower().lstrip(".")
        if extension not in PLUGIN_FILETYPES:
            continue
        data = (dataset / f["path"]).read_bytes()
        if extension in unsupported:
            with pytest.raises(ex.ExtractionError):
                ex.extract(data, filename=f["filename"])
            continue
        segments = ex.extract(data, filename=f["filename"])
        text = " ".join(s.text for s in segments)
        needs_ocr = any(s.meta.get("needs_ocr") for s in segments)
        assert needs_ocr or len(text.strip()) > 80, f["filename"]


def test_scans_have_no_text_layer(dataset: Path, manifest: dict[str, Any]) -> None:
    scans = [f for f in files(manifest) if f["filename"].startswith(("Scan_", "Zeitungsartikel"))]
    assert len(scans) >= 2
    for f in scans:
        segments = ex.extract_pdf((dataset / f["path"]).read_bytes())
        assert all(s.meta.get("needs_ocr") for s in segments), f["filename"]


def test_has_access_traps_for_students(manifest: dict[str, Any]) -> None:
    assert any(m.get("visible") is False for m in modules(manifest))
    assert any("available_from" in s for c in manifest["courses"] for s in c["sections"])
    enrolled = {
        user: {c["idnumber"] for c in manifest["courses"] if user in c["enrol"]}
        for user in ("teacher", "student", "student2")
    }
    # Each student misses a course the other one has, so cross-course leaks show.
    assert enrolled["student"] - enrolled["student2"]
    assert enrolled["student2"] - enrolled["student"]
    assert enrolled["teacher"] == {c["idnumber"] for c in manifest["courses"]}


def test_writes_probe_questions(dataset: Path) -> None:
    probes = (dataset / "PROBES.md").read_text()
    assert probes.count("\n| ") > 20
