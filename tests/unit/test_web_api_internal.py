"""Verifies the `/internal/*` endpoints `cron` and `matrix` call instead of
touching Store directly — see specs/015-microservice-split.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bsbot.config import Settings
from bsbot.ingest.model import ContentItem, ContentKind
from bsbot.web.app import create_app

HEADERS = {"Authorization": "Bearer s3cret"}


def doc(doc_id: str, kind: ContentKind, **kw: Any) -> ContentItem:
    defaults: dict[str, Any] = dict(
        doc_id=doc_id,
        course_id=1,
        course_name="LF05",
        section_name="Sec",
        module_id=1,
        module_name="Mod",
        modname="resource",
        title="T",
        kind=kind,
        header_path=["LF05", "Sec", "Mod"],
        timemodified=100,
    )
    return ContentItem(**{**defaults, **kw})


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("BSBOT_WEB__API_TOKEN", "s3cret")
    monkeypatch.setenv("BSBOT_GEMINI__API_KEY", "dummy-key")
    monkeypatch.setenv("BSBOT_GEMINI__EMBED_DIM", "2")
    monkeypatch.setenv("BSBOT_DATA_DIR", str(tmp_path))
    return Settings(_env_file=None)  # type: ignore[call-arg]


class _FakeEmbedder:
    def embed_documents(
        self, texts: list[str], *, skip_failures: bool = False
    ) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr("bsbot.web.app.GeminiEmbedder", lambda *a, **k: _FakeEmbedder())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_internal_endpoint_without_token_is_rejected(client: Any) -> None:
    response = client.post("/internal/crawl-result", json={"items": []})
    assert response.status_code == 401


class TestCrawlResult:
    def test_persists_and_reports_pending(self, client: Any) -> None:
        item = doc("a", ContentKind.INLINE, text="Hallo Welt")
        response = client.post(
            "/internal/crawl-result",
            json={"items": [item.model_dump(mode="json")]},
            headers=HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] == 1
        assert [d["doc_id"] for d in body["pending"]] == ["a"]


class TestFetchCache:
    def test_fetch_record_round_trips(self, client: Any) -> None:
        url = "https://moodle.example.de/file.pdf"
        missing = client.get("/internal/fetch-record", params={"url": url}, headers=HEADERS)
        assert missing.json() == {"record": None, "blob_exists": False}

        client.post(
            "/internal/fetch-record",
            json={"url": url, "sha256": "abc123", "fetched_at": 1, "checked_at": 1},
            headers=HEADERS,
        )
        found = client.get("/internal/fetch-record", params={"url": url}, headers=HEADERS)
        assert found.json()["record"]["sha256"] == "abc123"

    def test_fetch_failure_is_recorded(self, client: Any) -> None:
        url = "https://moodle.example.de/broken.pdf"
        response = client.post(
            "/internal/fetch-failures",
            json={"url": url, "error": "HTTP 404", "checked_at": 1},
            headers=HEADERS,
        )
        assert response.status_code == 200
        record = client.get("/internal/fetch-record", params={"url": url}, headers=HEADERS)
        assert record.json()["record"]["error"] == "HTTP 404"

    def test_blob_round_trips(self, client: Any) -> None:
        upload = client.post("/internal/blobs", content=b"hello world", headers=HEADERS)
        assert upload.status_code == 200
        sha256 = upload.json()["sha256"]

        download = client.get(f"/internal/blobs/{sha256}", headers=HEADERS)
        assert download.status_code == 200
        assert download.content == b"hello world"

    def test_missing_blob_is_404(self, client: Any) -> None:
        response = client.get("/internal/blobs/" + "0" * 64, headers=HEADERS)
        assert response.status_code == 404


class TestSegments:
    def test_indexes_a_document_from_resolved_segments(self, client: Any) -> None:
        item = doc("a", ContentKind.INLINE, text="placeholder")
        client.post(
            "/internal/crawl-result",
            json={"items": [item.model_dump(mode="json")]},
            headers=HEADERS,
        )
        response = client.post(
            "/internal/documents/a/segments",
            json={"segments": [{"text": "Die Prüfung ist am Montag."}], "blob_sha256": None},
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["indexed"] == 1

    def test_unknown_document_is_404(self, client: Any) -> None:
        response = client.post(
            "/internal/documents/does-not-exist/segments",
            json={"segments": None, "blob_sha256": None},
            headers=HEADERS,
        )
        assert response.status_code == 404


class TestEmbedPending:
    def test_embeds_chunks_with_no_vector_yet(self, client: Any) -> None:
        item = doc("a", ContentKind.INLINE, text="placeholder")
        client.post(
            "/internal/crawl-result",
            json={"items": [item.model_dump(mode="json")]},
            headers=HEADERS,
        )
        client.post(
            "/internal/documents/a/segments",
            json={"segments": [{"text": "Ein Satz zum Einbetten."}], "blob_sha256": None},
            headers=HEADERS,
        )
        response = client.post("/internal/embed-pending", headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {"embedded": 1, "total": 1}


class TestIngestMessage:
    def test_indexes_a_moderator_message(self, client: Any) -> None:
        response = client.post(
            "/internal/ingest-message",
            json={
                "room_id": "!room:example.org",
                "event_id": "$abc",
                "sender": "@teacher:example.org",
                "text": "Klausur verschoben auf Freitag.",
                "timemodified": 1,
            },
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert len(response.json()["chunk_ids"]) == 1
