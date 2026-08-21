"""Verifies spec 005 AC-13..AC-17 — Nextcloud public share handling."""

from __future__ import annotations

import pytest

from bsbot.ingest.nextcloud import ShareUnsupported, is_nextcloud_share, share_download_url


class TestRecognition:
    @pytest.mark.parametrize(
        "url",
        [
            "https://cloud.itech-bs14.de/index.php/s/37rmq3xrWBwxra3",
            "https://cloud.itech-bs14.de/s/39bL6sR2NDtzy2f",
            "https://cloud.example.org/index.php/s/AbC123?dir=/&editing=false",
        ],
    )
    def test_share_links_are_recognised(self, url: str) -> None:
        """AC-13"""
        assert is_nextcloud_share(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://docs.google.com/document/d/abc/edit",
            "https://hackmd.io/abc",
            "https://www.youtube.com/watch?v=abc",
            "https://moodle.itech-bs14.de/mod/page/view.php?id=1",
        ],
    )
    def test_other_links_are_not_shares(self, url: str) -> None:
        """AC-17: 97 external links on the live site; most are not fetchable."""
        assert not is_nextcloud_share(url)


class TestDownloadUrl:
    def test_index_php_form(self) -> None:
        """AC-13"""
        assert share_download_url("https://cloud.x.de/index.php/s/AbC123") == (
            "https://cloud.x.de/index.php/s/AbC123/download"
        )

    def test_short_form(self) -> None:
        assert share_download_url("https://cloud.x.de/s/AbC123") == (
            "https://cloud.x.de/s/AbC123/download"
        )

    def test_moodle_query_parameters_are_stripped(self) -> None:
        """AC-14: Moodle appends viewer state that breaks the download URL."""
        url = "https://cloud.x.de/index.php/s/AbC123?dir=/&editing=false&openfile=true"
        assert share_download_url(url) == "https://cloud.x.de/index.php/s/AbC123/download"

    def test_trailing_slash_tolerated(self) -> None:
        assert share_download_url("https://cloud.x.de/s/AbC123/") == (
            "https://cloud.x.de/s/AbC123/download"
        )

    def test_non_share_url_is_rejected(self) -> None:
        """AC-17"""
        with pytest.raises(ShareUnsupported):
            share_download_url("https://docs.google.com/document/d/abc/edit")
