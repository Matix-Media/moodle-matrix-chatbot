"""Verifies spec 011 AC-7..AC-9 — YouTube transcripts.

Verified live against a real corpus video (youtube.com/watch?v=AuqTOZq1sZw) before
writing this: no API key needed, German transcript fetched successfully.
"""

from __future__ import annotations

from bsbot.ingest.youtube import extract_video_id, fetch_transcript


class TestVideoIdExtraction:
    def test_watch_url(self) -> None:
        """AC-7"""
        assert extract_video_id("https://www.youtube.com/watch?v=AuqTOZq1sZw") == "AuqTOZq1sZw"

    def test_watch_url_with_extra_params(self) -> None:
        assert (
            extract_video_id("https://www.youtube.com/watch?v=AuqTOZq1sZw&t=42s") == "AuqTOZq1sZw"
        )

    def test_short_url(self) -> None:
        """AC-7"""
        assert extract_video_id("https://youtu.be/AuqTOZq1sZw") == "AuqTOZq1sZw"

    def test_short_url_with_query(self) -> None:
        assert extract_video_id("https://youtu.be/AuqTOZq1sZw?t=5") == "AuqTOZq1sZw"

    def test_non_youtube_url_returns_none(self) -> None:
        assert extract_video_id("https://example.com/watch?v=x") is None

    def test_watch_url_without_v_param_returns_none(self) -> None:
        assert extract_video_id("https://www.youtube.com/watch") is None


class FakeSnippet:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeTranscript:
    def __init__(self, language_code: str, snippets: list[str]) -> None:
        self.language_code = language_code
        self._snippets = snippets

    def fetch(self) -> list[FakeSnippet]:
        return [FakeSnippet(s) for s in self._snippets]


class FakeTranscriptList:
    def __init__(self, by_language: dict[str, list[str]]) -> None:
        self._by_language = by_language

    def find_transcript(self, language_codes: list[str]) -> FakeTranscript:
        for code in language_codes:
            if code in self._by_language:
                return FakeTranscript(code, self._by_language[code])
        raise LookupError("no matching transcript")

    def __iter__(self):
        return iter(FakeTranscript(lang, snips) for lang, snips in self._by_language.items())


class FakeApi:
    def __init__(self, boards: dict[str, dict[str, list[str]]] | None = None, fail: bool = False):
        self._boards = boards or {}
        self._fail = fail

    def list(self, video_id: str) -> FakeTranscriptList:
        if self._fail or video_id not in self._boards:
            raise Exception("no transcripts available")
        return FakeTranscriptList(self._boards[video_id])


class TestTranscriptFetching:
    def test_prefers_german(self) -> None:
        """AC-8"""
        api = FakeApi({"v1": {"de": ["Hallo", "Welt"], "en": ["Hello", "World"]}})
        text = fetch_transcript("v1", api=api)  # type: ignore[arg-type]
        assert text == "Hallo Welt"

    def test_falls_back_to_english(self) -> None:
        """AC-8"""
        api = FakeApi({"v1": {"en": ["Hello", "World"]}})
        text = fetch_transcript("v1", api=api)  # type: ignore[arg-type]
        assert text == "Hello World"

    def test_falls_back_to_any_available_language(self) -> None:
        """AC-8: neither preferred language exists, but something does."""
        api = FakeApi({"v1": {"fr": ["Bonjour", "monde"]}})
        text = fetch_transcript("v1", api=api)  # type: ignore[arg-type]
        assert text == "Bonjour monde"

    def test_no_captions_returns_none_not_an_error(self) -> None:
        """AC-9"""
        api = FakeApi(fail=True)
        assert fetch_transcript("v1", api=api) is None  # type: ignore[arg-type]

    def test_unknown_video_returns_none(self) -> None:
        """AC-9"""
        api = FakeApi({})
        assert fetch_transcript("missing", api=api) is None  # type: ignore[arg-type]
