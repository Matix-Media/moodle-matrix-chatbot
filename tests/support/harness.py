"""Integration harness: the real API app with a scripted model and a fake Moodle."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from moodle_llm_chat.access.tokens import issue_chat_token
from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.web.app import create_app
from support import HashEmbeddings, ScriptedModel

SITE = "schule"
MOODLE = "https://moodle.schule.test"
CALLBACK = f"{MOODLE}/local/llmchat/callback.php"
EMBED_DIM = 64


class FakeMoodle:
    """What the plugin's callback.php answers. Mutable per test."""

    def __init__(self) -> None:
        self.visible: dict[int, set[int]] = {}  # user → cmids Moodle shows right now
        self.groups: dict[int, dict[str, list[str]]] = {}
        self.down = False
        self.secret: str | None = None
        self.calls: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("moodle down")
        if request.headers.get("authorization") != f"Bearer {self.secret}":
            return httpx.Response(401)
        body = json.loads(request.content)
        self.calls.append(body)
        user = int(body["userid"])
        if body["action"] == "check_access":
            allowed = [c for c in body["cmids"] if c in self.visible.get(user, set())]
            return httpx.Response(200, json={"allowed": allowed})
        if body["action"] == "user_info":
            return httpx.Response(200, json={"groups": self.groups.get(user, {})})
        return httpx.Response(400)


class Api:
    def __init__(self, data_dir: Path, master: bytes, moodle: FakeMoodle) -> None:
        self.data_dir = data_dir
        self.master = master
        self.moodle = moodle
        self.model = ScriptedModel()
        self.embeddings = HashEmbeddings(EMBED_DIM)
        self.client: TestClient | None = None
        #: The active respx router: mock external sites with ``api.web.get(...)``.
        self.web: Any = None
        self.secret = ""
        self._n = 0

    # -- lifecycle -------------------------------------------------------

    @contextmanager
    def running(self) -> Iterator[Api]:
        app = create_app(
            Settings(),
            ring=KeyRing(self.master),
            chat_model=self.model,
            embeddings=self.embeddings,
        )
        with TestClient(app, base_url="https://testserver") as client:
            self.client = client
            if not self.secret:
                self.secret = self.on_loop(
                    lambda: self.store.add_site(SITE, name="Schule", url=MOODLE)
                )
                self.moodle.secret = self.secret
            try:
                yield self
            finally:
                self.client = None

    @property
    def http(self) -> TestClient:
        assert self.client is not None, "API is not running"
        return self.client

    @property
    def store(self) -> Store:
        store: Store = self.http.app.state.store  # type: ignore[attr-defined]
        return store

    def on_loop(self, fn: Callable[..., Any], *args: Any) -> Any:
        assert self.http.portal is not None
        return self.http.portal.call(fn, *args)

    def idle(self, timeout: float = 15.0) -> None:
        pipeline = self.http.app.state.pipeline  # type: ignore[attr-defined]
        assert self.http.portal is not None
        self.http.portal.call(lambda: pipeline.idle(timeout=timeout))

    # -- the plugin's side ------------------------------------------------

    @property
    def site_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    def push_courses(self, *courses: dict[str, Any]) -> None:
        r = self.http.post(
            "/ingest/courses", json={"courses": list(courses)}, headers=self.site_headers
        )
        assert r.status_code == 200, r.text

    def push_documents(self, *docs: dict[str, Any], sync_id: str | None = None) -> dict[str, Any]:
        r = self.http.post(
            "/ingest/documents",
            json={"documents": list(docs), "sync_id": sync_id},
            headers=self.site_headers,
        )
        assert r.status_code == 200, r.text
        result: dict[str, Any] = r.json()
        return result

    def upload(self, data: bytes) -> None:
        sha = hashlib.sha256(data).hexdigest()
        r = self.http.put(f"/ingest/blobs/{sha}", content=data, headers=self.site_headers)
        assert r.status_code in (200, 201), r.text

    def token(self, user: int, *, cmids: set[int], courses: set[int], **claims: Any) -> str:
        self._n += 1
        now = int(time.time())
        base: dict[str, Any] = {
            "iss": MOODLE,
            "sub": str(user),
            "name": f"Nutzer {user}",
            "lang": "de",
            "caps": ["read", "interact"],
            "courses": sorted(courses),
            "roles": {str(c): ["student"] for c in courses},
            "cmids": sorted(cmids),
            "jti": f"it-{self._n}-{now}",
            "iat": now,
            "exp": now + 600,
        }
        base.update(claims)
        return issue_chat_token(self.secret, SITE, base)


def document(
    doc_id: str,
    *,
    kind: str = "inline",
    text: str | None = None,
    section: str = "Woche 1",
    section_order: int = 1,
    module: str | None = None,
    title: str | None = None,
    timemodified: int = 1_790_000_000,
    **extra: Any,
) -> dict[str, Any]:
    course_id, cmid, _ = doc_id.split(":")
    name = module or title or doc_id
    body: dict[str, Any] = {
        "doc_id": doc_id,
        "course_id": int(course_id),
        "section_id": section_order,
        "section_name": section,
        "section_order": section_order,
        "cmid": int(cmid),
        "modname": "page",
        "module_name": name,
        "title": title or name,
        "kind": kind,
        "header_path": [f"Kurs {course_id}", section, name],
        "module_url": f"{MOODLE}/mod/page/view.php?id={cmid}",
        "timemodified": timemodified,
        "text": text,
        "content_hash": hashlib.sha256(f"{doc_id}{text}{extra}".encode()).hexdigest()[:16],
    }
    body.update(extra)
    return body


def file_document(doc_id: str, data: bytes, filename: str, **kw: Any) -> dict[str, Any]:
    return document(
        doc_id,
        kind="file",
        file={
            "sha256": hashlib.sha256(data).hexdigest(),
            "filename": filename,
            "mimetype": "application/pdf" if filename.endswith(".pdf") else None,
            "filesize": len(data),
        },
        **kw,
    )


def ws_until(ws: Any, kind: str, limit: int = 80) -> list[dict[str, Any]]:
    """Collect frames up to ``kind``; fail fast on an unexpected ``error`` frame."""
    frames: list[dict[str, Any]] = []
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == kind:
            return frames
        if frame["type"] == "error" and kind != "error":
            raise AssertionError(f"error frame while waiting for {kind!r}: {frame}")
    raise AssertionError(f"no {kind!r} frame in {[f['type'] for f in frames]}")


def read_disk(root: Path) -> bytes:
    data = b""
    for path in root.rglob("*"):
        if path.is_file():
            data += path.read_bytes()
    return data
