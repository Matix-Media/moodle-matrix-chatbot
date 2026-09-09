"""Verifies `CronApiClient` — cron's HTTP client for `api`, replacing direct
Store/Fetcher-with-a-Store access. See specs/019-microservice-split.md.
"""

from __future__ import annotations

import httpx
import respx

from bsbot.ingest.api_client import CronApiClient
from bsbot.ingest.model import ContentItem, ContentKind

BASE_URL = "http://api:8000"


def make_client() -> CronApiClient:
    return CronApiClient(BASE_URL, "s3cret")


@respx.mock
def test_fetch_record_returns_none_when_absent() -> None:
    respx.get(f"{BASE_URL}/internal/fetch-record").mock(
        return_value=httpx.Response(200, json={"record": None, "blob_exists": False})
    )
    assert make_client().fetch_record("https://example.de/x.pdf") is None


@respx.mock
def test_fetch_record_parses_a_present_record() -> None:
    record = {
        "url": "https://example.de/x.pdf",
        "sha256": "abc",
        "etag": None,
        "last_modified": None,
        "moodle_timemodified": None,
        "fetched_at": 1,
        "checked_at": 1,
        "error": None,
    }
    respx.get(f"{BASE_URL}/internal/fetch-record").mock(
        return_value=httpx.Response(200, json={"record": record, "blob_exists": True})
    )
    result = make_client().fetch_record("https://example.de/x.pdf")
    assert result is not None
    assert result.sha256 == "abc"


@respx.mock
def test_blob_exists_uses_head() -> None:
    route = respx.head(f"{BASE_URL}/internal/blobs/abc").mock(return_value=httpx.Response(200))
    assert make_client().blob_exists("abc") is True
    assert route.called


@respx.mock
def test_blob_missing_returns_false() -> None:
    respx.head(f"{BASE_URL}/internal/blobs/missing").mock(return_value=httpx.Response(404))
    assert make_client().blob_exists("missing") is False


@respx.mock
def test_put_blob_posts_raw_bytes_and_returns_sha256() -> None:
    route = respx.post(f"{BASE_URL}/internal/blobs").mock(
        return_value=httpx.Response(200, json={"sha256": "digest123"})
    )
    assert make_client().put_blob(b"hello") == "digest123"
    assert route.calls.last.request.content == b"hello"


@respx.mock
def test_crawl_result_sends_serialized_items_and_parses_pending() -> None:
    item = ContentItem(
        doc_id="a",
        course_id=1,
        course_name="LF05",
        section_name="Sec",
        module_id=1,
        module_name="Mod",
        modname="resource",
        title="T",
        kind=ContentKind.INLINE,
        header_path=["LF05"],
        text="hi",
    )
    pending_doc = {
        "doc_id": "a",
        "course_id": 1,
        "course_name": "LF05",
        "section_name": "Sec",
        "module_id": 1,
        "module_name": "Mod",
        "modname": "resource",
        "title": "T",
        "kind": "inline",
        "header_path": ["LF05"],
        "module_url": None,
        "timemodified": 0,
        "text": "hi",
        "file_url": None,
        "filename": None,
        "filesize": 0,
        "mimetype": None,
        "external_url": None,
        "extractable": True,
        "skip_reason": None,
        "text_sha256": None,
        "blob_sha256": None,
        "extract_version": None,
        "first_seen": 0,
        "last_seen": 0,
        "tombstoned_at": None,
    }
    route = respx.post(f"{BASE_URL}/internal/crawl-result").mock(
        return_value=httpx.Response(200, json={"persisted": 1, "pending": [pending_doc]})
    )
    persisted, pending = make_client().crawl_result([item])
    assert persisted == 1
    assert [d.doc_id for d in pending] == ["a"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer s3cret"


@respx.mock
def test_embed_pending_returns_the_response_body() -> None:
    respx.post(f"{BASE_URL}/internal/embed-pending").mock(
        return_value=httpx.Response(200, json={"embedded": 3, "total": 5})
    )
    assert make_client().embed_pending() == {"embedded": 3, "total": 5}
