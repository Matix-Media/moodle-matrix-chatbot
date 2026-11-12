"""External URL modules: which adapter handles a link, and how it is fetched."""

from __future__ import annotations

import datetime
import email.utils
from typing import Any

import httpx
import pytest
import respx

from moodle_llm_chat.ingest.sources import (
    FetchedBytes,
    FetchedText,
    PermanentFetchError,
    TransientFetchError,
    fetch_external,
)


class FakeTranscripts:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def list(self, video_id: str) -> Any:
        if self.text is None:
            raise RuntimeError("no captions")
        text = self.text

        class Snip:
            def __init__(self, t: str) -> None:
                self.text = t

        class T:
            def fetch(self) -> list[Snip]:
                return [Snip(text)]

        class L:
            def find_transcript(self, langs: list[str]) -> T:
                return T()

            def __iter__(self) -> Any:
                return iter([T()])

        return L()


async def _fetch(url: str, **kw: Any) -> FetchedText | FetchedBytes | None:
    async with httpx.AsyncClient() as http:
        return await fetch_external(url, title="Titel", http=http, max_bytes=1000, **kw)


class TestRouting:
    async def test_youtube_yields_transcript_text(self) -> None:
        got = await _fetch("https://youtu.be/dQw4w9WgXcQ", youtube_api=FakeTranscripts("Hallo"))
        assert got == FetchedText(text="Hallo")

    async def test_youtube_without_captions_is_skipped(self) -> None:
        assert (
            await _fetch(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", youtube_api=FakeTranscripts(None)
            )
            is None
        )

    async def test_taskcards_board_is_rendered(self) -> None:
        async def fake_board(http: Any, host: str, board_id: str, **kw: Any) -> dict[str, Any]:
            return {"name": "Board", "lists": [], "cards": []}

        got = await _fetch(
            "https://bs14.taskcards.app/#/board/12345678-1234-1234-1234-123456789012/view",
            taskcards_fetch=fake_board,
        )
        assert isinstance(got, FetchedText)
        assert "Board" in got.text

    @respx.mock
    async def test_hackmd_note_is_fetched_as_markdown(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(200, text="# Notiz")
        got = await _fetch("https://hackmd.io/abcDEF123")
        assert got == FetchedBytes(data=b"# Notiz", filename="note.md")

    @respx.mock
    async def test_google_doc_uses_export_url(self) -> None:
        route = respx.get(
            url__regex=r"https://docs\.google\.com/document/d/DOCID/export.*"
        ).respond(200, content=b"Text")
        got = await _fetch("https://docs.google.com/document/d/DOCID/edit")
        assert route.called
        assert isinstance(got, FetchedBytes)
        assert got.filename == "export.txt"

    async def test_unknown_site_is_skipped(self) -> None:
        """Arbitrary web pages are not crawled — only known document hosts."""
        assert await _fetch("https://example.com/some/article") is None


class TestFailures:
    @respx.mock
    async def test_server_error_is_transient(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(503)
        with pytest.raises(TransientFetchError):
            await _fetch("https://hackmd.io/abcDEF123")

    @respx.mock
    async def test_rate_limit_carries_retry_after_seconds(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(429, headers={"Retry-After": "120"})
        with pytest.raises(TransientFetchError) as err:
            await _fetch("https://hackmd.io/abcDEF123")
        assert err.value.retry_after == 120
        assert err.value.rate_limited

    @respx.mock
    async def test_retry_after_http_date(self) -> None:
        later = email.utils.format_datetime(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10), usegmt=True
        )
        respx.get("https://hackmd.io/abcDEF123.md").respond(503, headers={"Retry-After": later})
        with pytest.raises(TransientFetchError) as err:
            await _fetch("https://hackmd.io/abcDEF123")
        assert err.value.retry_after is not None and 500 < err.value.retry_after <= 600

    @respx.mock
    async def test_rate_limit_without_header_has_no_retry_after(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(429)
        with pytest.raises(TransientFetchError) as err:
            await _fetch("https://hackmd.io/abcDEF123")
        assert err.value.retry_after is None and err.value.rate_limited

    @respx.mock
    async def test_not_found_is_permanent(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(404)
        with pytest.raises(PermanentFetchError):
            await _fetch("https://hackmd.io/abcDEF123")

    @respx.mock
    async def test_network_error_is_transient(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(TransientFetchError):
            await _fetch("https://hackmd.io/abcDEF123")

    @respx.mock
    async def test_oversized_download_is_permanent(self) -> None:
        respx.get("https://hackmd.io/abcDEF123.md").respond(200, content=b"x" * 5000)
        with pytest.raises(PermanentFetchError, match="large"):
            await _fetch("https://hackmd.io/abcDEF123")

    async def test_non_http_url_is_permanent(self) -> None:
        with pytest.raises(PermanentFetchError):
            await _fetch("file:///etc/passwd")
