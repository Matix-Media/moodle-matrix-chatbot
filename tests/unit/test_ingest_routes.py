"""Ingest API: the plugin pushes courses, documents and blobs with its site secret."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from factories import doc, file_doc
from fastapi.testclient import TestClient

from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.web.app import create_app


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MLC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MLC_EMBEDDING__DIM", "3")
    monkeypatch.setenv("MLC_PIPELINE__MAX_FILE_BYTES", "1000")
    app = create_app(Settings(), ring=KeyRing(os.urandom(32)), start_workers=False)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def on_loop(client: TestClient, fn: Callable[..., Any], *args: Any) -> Any:
    """Run ``fn`` on the app's event loop thread, which owns the store connection."""
    assert client.portal is not None
    return client.portal.call(fn, *args)


def store_of(client: TestClient) -> Store:
    store: Store = client.app.state.store  # type: ignore[attr-defined]
    return store


@pytest.fixture
def secrets(app_client: TestClient) -> dict[str, str]:
    store = store_of(app_client)
    return {
        "a": on_loop(
            app_client, lambda: store.add_site("site-a", name="A", url="https://moodle.a.test")
        ),
        "b": on_loop(
            app_client, lambda: store.add_site("site-b", name="B", url="https://moodle.b.test")
        ),
    }


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _docs(*items: Any) -> dict[str, Any]:
    return {"documents": [d.model_dump(mode="json") for d in items]}


class TestAuth:
    def test_health_needs_no_auth(self, app_client: TestClient) -> None:
        assert app_client.get("/healthz").status_code == 200

    def test_missing_secret_is_401(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        assert app_client.get("/ingest/status").status_code == 401

    def test_wrong_secret_is_401_with_generic_body(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        r = app_client.get("/ingest/status", headers=_auth("mlc_wrong"))
        assert r.status_code == 401
        assert r.json() == {"detail": "unauthorized"}

    def test_revoked_site_is_401(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        on_loop(app_client, store_of(app_client).revoke_site, "site-a")
        assert app_client.get("/ingest/status", headers=_auth(secrets["a"])).status_code == 401

    def test_plain_http_is_refused(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        r = app_client.get("http://testserver/ingest/status", headers=_auth(secrets["a"]))
        assert r.status_code == 403
        assert "TLS" in r.json()["detail"]

    def test_valid_secret(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        r = app_client.get("/ingest/status", headers=_auth(secrets["a"]))
        assert r.status_code == 200
        assert r.json()["documents"] == 0

    def test_status_names_the_site(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        """The plugin learns its site id (the chat token's `kid`) from this response."""
        r = app_client.get("/ingest/status", headers=_auth(secrets["a"]))
        assert r.json()["site_id"] == "site-a"
        r = app_client.get("/ingest/status", headers=_auth(secrets["b"]))
        assert r.json()["site_id"] == "site-b"


class TestDocuments:
    def test_upsert_reports_changed_and_missing_blobs(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        data = b"%PDF-1.4"
        sha = hashlib.sha256(data).hexdigest()
        r = app_client.post(
            "/ingest/documents",
            json=_docs(doc("2:10:intro"), file_doc("2:11:f", sha)),
            headers=_auth(secrets["a"]),
        )
        assert r.status_code == 200
        assert r.json() == {"changed": ["2:10:intro", "2:11:f"], "needs_blob": [sha]}

    def test_resend_is_idempotent(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        body = _docs(doc("2:10:intro"))
        app_client.post("/ingest/documents", json=body, headers=_auth(secrets["a"]))
        r = app_client.post("/ingest/documents", json=body, headers=_auth(secrets["a"]))
        assert r.json()["changed"] == []

    def test_invalid_document_is_422(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        bad = doc().model_dump(mode="json") | {"kind": "file", "text": None}
        r = app_client.post(
            "/ingest/documents", json={"documents": [bad]}, headers=_auth(secrets["a"])
        )
        assert r.status_code == 422

    def test_sites_cannot_see_each_others_documents(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        app_client.post("/ingest/documents", json=_docs(doc()), headers=_auth(secrets["a"]))
        status_b = app_client.get("/ingest/status", headers=_auth(secrets["b"])).json()
        assert status_b["documents"] == 0
        r = app_client.post(
            "/ingest/documents/delete",
            json={"doc_ids": ["2:10:intro"]},
            headers=_auth(secrets["b"]),
        )
        assert r.status_code == 200
        status_a = app_client.get("/ingest/status", headers=_auth(secrets["a"])).json()
        assert status_a["documents"] == 1

    def test_delete(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        app_client.post("/ingest/documents", json=_docs(doc()), headers=_auth(secrets["a"]))
        r = app_client.post(
            "/ingest/documents/delete",
            json={"doc_ids": ["2:10:intro", "unknown"]},
            headers=_auth(secrets["a"]),
        )
        assert r.status_code == 200
        assert (
            app_client.get("/ingest/status", headers=_auth(secrets["a"])).json()["documents"] == 0
        )

    def test_changed_documents_are_announced_to_the_pipeline(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        seen: list[tuple[str, list[str], bool]] = []
        app_client.app.state.document_listeners.append(  # type: ignore[attr-defined]
            lambda site, ids, bulk: seen.append((site, ids, bulk))
        )
        app_client.post("/ingest/documents", json=_docs(doc()), headers=_auth(secrets["a"]))
        assert seen == [("site-a", ["2:10:intro"], False)]

    def test_renamed_section_is_announced_as_relocation_not_change(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        h = _auth(secrets["a"])
        app_client.post("/ingest/documents", json=_docs(doc()), headers=h)
        changed: list[list[str]] = []
        relocated: list[tuple[str, list[str]]] = []
        state = app_client.app.state  # type: ignore[attr-defined]
        state.document_listeners.append(lambda site, ids, bulk: changed.append(ids))
        state.relocation_listeners.append(lambda site, ids: relocated.append((site, ids)))
        renamed = doc(section_name="Neu", header_path=["Netzwerke", "Neu", "Einführung"])
        r = app_client.post("/ingest/documents", json=_docs(renamed), headers=h)
        assert r.json()["changed"] == []
        assert changed == [] and relocated == [("site-a", ["2:10:intro"])]

    def test_documents_are_queued_for_processing(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        app_client.post("/ingest/documents", json=_docs(doc()), headers=_auth(secrets["a"]))
        status = app_client.get("/ingest/status", headers=_auth(secrets["a"])).json()
        assert status["pipeline"]["stages"]["extract"]["queued"] == 1


class TestCourses:
    def test_upsert_and_delete(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        course = {"course_id": 2, "fullname": "Netzwerke", "shortname": "LF7"}
        r = app_client.post(
            "/ingest/courses", json={"courses": [course]}, headers=_auth(secrets["a"])
        )
        assert r.status_code == 200
        assert app_client.get("/ingest/status", headers=_auth(secrets["a"])).json()["courses"] == 1
        app_client.post(
            "/ingest/courses/delete", json={"course_ids": [2]}, headers=_auth(secrets["a"])
        )
        assert app_client.get("/ingest/status", headers=_auth(secrets["a"])).json()["courses"] == 0


class TestBlobs:
    def test_upload_and_digest_check(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        data = b"%PDF-1.4 inhalt"
        sha = hashlib.sha256(data).hexdigest()
        app_client.post(
            "/ingest/documents", json=_docs(file_doc("2:11:f", sha)), headers=_auth(secrets["a"])
        )
        r = app_client.put(f"/ingest/blobs/{sha}", content=data, headers=_auth(secrets["a"]))
        assert r.status_code == 201
        again = app_client.put(f"/ingest/blobs/{sha}", content=data, headers=_auth(secrets["a"]))
        assert again.status_code == 200
        assert on_loop(app_client, store_of(app_client).read_blob, sha) == data

    def test_digest_mismatch_is_422(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        sha = hashlib.sha256(b"expected").hexdigest()
        app_client.post(
            "/ingest/documents", json=_docs(file_doc("2:11:f", sha)), headers=_auth(secrets["a"])
        )
        r = app_client.put(f"/ingest/blobs/{sha}", content=b"other", headers=_auth(secrets["a"]))
        assert r.status_code == 422

    def test_too_large_is_413(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        data = b"x" * 2000
        sha = hashlib.sha256(data).hexdigest()
        app_client.post(
            "/ingest/documents", json=_docs(file_doc("2:11:f", sha)), headers=_auth(secrets["a"])
        )
        r = app_client.put(f"/ingest/blobs/{sha}", content=data, headers=_auth(secrets["a"]))
        assert r.status_code == 413

    def test_unreferenced_blob_is_refused(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        """A site may only upload blobs its own documents reference."""
        data = b"random"
        sha = hashlib.sha256(data).hexdigest()
        r = app_client.put(f"/ingest/blobs/{sha}", content=data, headers=_auth(secrets["a"]))
        assert r.status_code == 404

    def test_bad_sha_in_path_is_422(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        r = app_client.put("/ingest/blobs/xyz", content=b"a", headers=_auth(secrets["a"]))
        assert r.status_code == 422


class TestSync:
    def test_full_sync_lifecycle(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        h = _auth(secrets["a"])
        app_client.post("/ingest/documents", json=_docs(doc("2:10:a"), doc("2:11:b")), headers=h)
        assert (
            app_client.post(
                "/ingest/sync/start", json={"sync_id": "s1", "mode": "full"}, headers=h
            ).status_code
            == 200
        )
        need = app_client.post(
            "/ingest/manifest",
            json={"sync_id": "s1", "entries": {"2:10:a": "h-2:10:a", "2:12:c": "h-c"}},
            headers=h,
        ).json()["need"]
        assert need == ["2:12:c"]
        app_client.post(
            "/ingest/documents",
            json={"sync_id": "s1", **_docs(doc("2:12:c", content_hash="h-c"))},
            headers=h,
        )
        result = app_client.post("/ingest/sync/finish", json={"sync_id": "s1"}, headers=h).json()
        assert result["tombstoned"] == ["2:11:b"]
        # A retried finish is harmless.
        again = app_client.post("/ingest/sync/finish", json={"sync_id": "s1"}, headers=h)
        assert again.status_code == 200
        assert again.json()["tombstoned"] == []

    def test_finish_unknown_run_is_409(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        r = app_client.post(
            "/ingest/sync/finish", json={"sync_id": "nope"}, headers=_auth(secrets["a"])
        )
        assert r.status_code == 409

    def test_manifest_for_closed_run_is_409(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        r = app_client.post(
            "/ingest/manifest",
            json={"sync_id": "nope", "entries": {}},
            headers=_auth(secrets["a"]),
        )
        assert r.status_code == 409


class TestPluginWireShapes:
    """The exact bodies local_llmchat's api_client sends (bare arrays, list manifests)."""

    def test_bare_arrays_and_list_manifest(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        h = _auth(secrets["a"])
        course = {"course_id": 2, "fullname": "Netzwerke", "shortname": "LF7", "visible": True}
        assert app_client.post("/ingest/courses", json=[course], headers=h).status_code == 200
        old = doc("2:9:old").model_dump(mode="json")
        assert app_client.post("/ingest/documents", json=[old], headers=h).status_code == 200
        app_client.post("/ingest/sync/start", json={"sync_id": "s1", "mode": "full"}, headers=h)
        manifest = {
            "sync_id": "s1",
            "chunk_no": 1,
            "of": 1,
            "entries": [{"doc_id": "2:10:a", "timemodified": 1, "content_hash": "h-2:10:a"}],
        }
        r = app_client.post("/ingest/manifest", json=manifest, headers=h)
        assert r.status_code == 200 and r.json()["need"] == ["2:10:a"]
        app_client.post("/ingest/courses", json=[course], headers=h)
        r = app_client.post(
            "/ingest/documents", json=[doc("2:10:a").model_dump(mode="json")], headers=h
        )
        assert r.json()["changed"] == ["2:10:a"]
        result = app_client.post("/ingest/sync/finish", json={"sync_id": "s1"}, headers=h).json()
        assert result["tombstoned"] == ["2:9:old"]
        status = app_client.get("/ingest/status", headers=h).json()
        assert status["documents"] == 1 and status["courses"] == 1

    def test_list_manifest_with_placement_hash(
        self, app_client: TestClient, secrets: dict[str, str]
    ) -> None:
        h = _auth(secrets["a"])
        placed = doc("2:10:a", placement_hash="p1").model_dump(mode="json")
        app_client.post("/ingest/documents", json=[placed], headers=h)
        app_client.post("/ingest/sync/start", json={"sync_id": "s1", "mode": "full"}, headers=h)
        entry = {"doc_id": "2:10:a", "timemodified": 1, "content_hash": "h-2:10:a"}
        manifest = {"sync_id": "s1", "entries": [{**entry, "placement_hash": "p1"}]}
        assert app_client.post("/ingest/manifest", json=manifest, headers=h).json()["need"] == []
        manifest = {"sync_id": "s1", "entries": [{**entry, "placement_hash": "p2"}]}
        r = app_client.post("/ingest/manifest", json=manifest, headers=h)
        assert r.json()["need"] == ["2:10:a"]

    def test_bare_array_delete(self, app_client: TestClient, secrets: dict[str, str]) -> None:
        h = _auth(secrets["a"])
        app_client.post("/ingest/documents", json=[doc().model_dump(mode="json")], headers=h)
        r = app_client.post("/ingest/documents/delete", json=["2:10:intro"], headers=h)
        assert r.status_code == 200
        assert app_client.get("/ingest/status", headers=h).json()["documents"] == 0
