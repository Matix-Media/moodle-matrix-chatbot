"""Verifies spec 011 AC-1..AC-2 — HackMD notes.

Appending .md to a note URL returns raw Markdown, confirmed against HackMD's own
docs. Both URL shapes tested here (bare id, @user/id) are real, found on the live
corpus.
"""

from __future__ import annotations

import pytest

from bsbot.cron.hackmd import HackmdUnsupported, hackmd_markdown_url, is_hackmd_note


class TestRecognition:
    @pytest.mark.parametrize(
        "url",
        [
            "https://hackmd.io/@MHeinemann/SyN6uPIVD",
            "https://hackmd.io/GQjkyUd_QXCOtCosTsBtNw",
            "https://hackmd.io/@Hitech/SkZum2AVkl#/",
        ],
    )
    def test_hackmd_urls_are_recognised(self, url: str) -> None:
        assert is_hackmd_note(url)

    @pytest.mark.parametrize("url", ["https://hackmd.io/", "https://example.com/@user/note"])
    def test_other_urls_are_not_recognised(self, url: str) -> None:
        assert not is_hackmd_note(url)


class TestMarkdownUrl:
    def test_bare_note_id(self) -> None:
        assert (
            hackmd_markdown_url("https://hackmd.io/GQjkyUd_QXCOtCosTsBtNw")
            == "https://hackmd.io/GQjkyUd_QXCOtCosTsBtNw.md"
        )

    def test_user_prefixed_note(self) -> None:
        assert (
            hackmd_markdown_url("https://hackmd.io/@MHeinemann/SyN6uPIVD")
            == "https://hackmd.io/@MHeinemann/SyN6uPIVD.md"
        )

    def test_trailing_fragment_is_stripped(self) -> None:
        """Real corpus URLs carry a bare '#/' fragment from the editor view."""
        assert (
            hackmd_markdown_url("https://hackmd.io/@Hitech/SkZum2AVkl#/")
            == "https://hackmd.io/@Hitech/SkZum2AVkl.md"
        )

    def test_non_hackmd_url_is_rejected(self) -> None:
        with pytest.raises(HackmdUnsupported):
            hackmd_markdown_url("https://example.com/note")
