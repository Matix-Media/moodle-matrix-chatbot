"""HTTP client for `api` used by `cron` — replaces all of cron's direct Store
access. See specs/015-microservice-split.md.

Implements `bsbot.ingest.fetcher.FetchCache` (so the existing, unmodified
`Fetcher` can run with no Store at all) plus the handful of calls
`sync_loop.py`'s cron flow makes directly: persisting a crawl, submitting
resolved segments for indexing, and triggering embedding. Every call blocks
synchronously (`httpx.Client`, not async) — `Fetcher` itself already calls
`FetchCache` methods synchronously today (they used to be fast local sqlite
calls; now they're fast internal HTTP calls instead, same tradeoff
`bsbot.matrix.api_client.ApiClient` already makes).
"""

from __future__ import annotations

import httpx

from bsbot.index.store import Document, FetchRecord
from bsbot.ingest.chunk import Segment
from bsbot.ingest.model import ContentItem


class CronApiClient:
    def __init__(self, base_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- FetchCache (bsbot.ingest.fetcher) ------------------------------ #

    def fetch_record(self, url: str) -> FetchRecord | None:
        response = self._get("/internal/fetch-record", params={"url": url})
        record = response.json()["record"]
        return FetchRecord(**record) if record else None

    def blob_exists(self, sha256: str) -> bool:
        response = self._http.head(f"/internal/blobs/{sha256}", headers=self._headers)
        return response.status_code == 200

    def read_blob(self, sha256: str) -> bytes:
        response = self._get(f"/internal/blobs/{sha256}")
        return response.content

    def put_blob(self, data: bytes) -> str:
        response = self._http.post("/internal/blobs", content=data, headers=self._headers)
        response.raise_for_status()
        return str(response.json()["sha256"])

    def record_fetch(
        self,
        url: str,
        *,
        sha256: str | None,
        etag: str | None = None,
        last_modified: str | None = None,
        moodle_timemodified: int | None = None,
        fetched_at: int | None = None,
        checked_at: int | None = None,
    ) -> None:
        self._post(
            "/internal/fetch-record",
            json={
                "url": url,
                "sha256": sha256,
                "etag": etag,
                "last_modified": last_modified,
                "moodle_timemodified": moodle_timemodified,
                "fetched_at": fetched_at,
                "checked_at": checked_at,
            },
        )

    def record_fetch_failure(self, url: str, *, error: str, checked_at: int | None = None) -> None:
        self._post(
            "/internal/fetch-failures", json={"url": url, "error": error, "checked_at": checked_at}
        )

    def touch_fetch(self, url: str, *, checked_at: int) -> None:
        self._post("/internal/fetch-record/touch", json={"url": url, "checked_at": checked_at})

    # -- cron orchestration --------------------------------------------- #

    def crawl_result(self, items: list[ContentItem]) -> tuple[int, list[Document]]:
        dumped = [item.model_dump(mode="json") for item in items]
        response = self._post("/internal/crawl-result", json={"items": dumped})
        body = response.json()
        return int(body["persisted"]), [Document(**d) for d in body["pending"]]

    def pending_documents(self) -> list[Document]:
        response = self._get("/internal/documents/pending")
        return [Document(**d) for d in response.json()]

    def index_segments(
        self, doc_id: str, segments: list[Segment] | None, blob_sha256: str | None
    ) -> dict[str, int]:
        payload = {
            "segments": [
                {"text": s.text, "page": s.page, "label": s.label, "meta": s.meta} for s in segments
            ]
            if segments is not None
            else None,
            "blob_sha256": blob_sha256,
        }
        response = self._post(f"/internal/documents/{doc_id}/segments", json=payload)
        return dict(response.json())

    def embed_pending(self) -> dict[str, int]:
        return dict(self._post("/internal/embed-pending").json())

    def _get(self, path: str, **kwargs: object) -> httpx.Response:
        response = self._http.get(path, headers=self._headers, **kwargs)  # type: ignore[arg-type]
        response.raise_for_status()
        return response

    def _post(self, path: str, **kwargs: object) -> httpx.Response:
        response = self._http.post(path, headers=self._headers, **kwargs)  # type: ignore[arg-type]
        response.raise_for_status()
        return response
