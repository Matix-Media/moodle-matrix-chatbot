"""Verifies spec 011 AC-3..AC-6 — Google Docs/Slides/Sheets export URLs.

Every real link in the corpus (verified by direct query) is one of these three
types. Export URLs work with no auth only for documents shared "anyone with the
link" — a restricted document must fail the fetch cleanly (AC-6), covered in the
indexer-level tests rather than here since it depends on the actual HTTP response.
"""

from __future__ import annotations

import pytest

from bsbot.cron.google_docs import GoogleDocsUnsupported, google_export_url


class TestDocument:
    def test_document_exports_as_text(self) -> None:
        """AC-3"""
        url, extractor_kind = google_export_url(
            "https://docs.google.com/document/d/1vh6fG6P_HtIIpKSLEgGDFDgOZBBTPT0GrWFehYBYz4s/edit?usp=sharing"
        )
        assert url == (
            "https://docs.google.com/document/d/1vh6fG6P_HtIIpKSLEgGDFDgOZBBTPT0GrWFehYBYz4s"
            "/export?format=txt"
        )
        assert extractor_kind == "text"


class TestPresentation:
    def test_presentation_exports_as_pptx(self) -> None:
        """AC-4"""
        url, extractor_kind = google_export_url(
            "https://docs.google.com/presentation/d/104J0fmocQcs1W_tsSKIJh6AMTQwLFDHjt6ScYyRQgaA/edit?usp=sharing"
        )
        assert url == (
            "https://docs.google.com/presentation/d/104J0fmocQcs1W_tsSKIJh6AMTQwLFDHjt6ScYyRQgaA"
            "/export/pptx"
        )
        assert extractor_kind == "pptx"

    def test_slide_fragment_is_ignored(self) -> None:
        url, _ = google_export_url(
            "https://docs.google.com/presentation/d/1QKypoTVhvRLEOtk03glDun2jgbC2OJ2Zx5N--68Pcp8/edit?slide=id.p#"
        )
        assert url == (
            "https://docs.google.com/presentation/d/1QKypoTVhvRLEOtk03glDun2jgbC2OJ2Zx5N--68Pcp8"
            "/export/pptx"
        )


class TestSpreadsheet:
    def test_spreadsheet_exports_as_xlsx(self) -> None:
        """AC-5"""
        url, extractor_kind = google_export_url(
            "https://docs.google.com/spreadsheets/d/19caGhA_u-A_egXEewKrLsaSs5LBvOXG9T-tsDKsiJAU/edit?usp=sharing"
        )
        assert url == (
            "https://docs.google.com/spreadsheets/d/19caGhA_u-A_egXEewKrLsaSs5LBvOXG9T-tsDKsiJAU"
            "/export?format=xlsx"
        )
        assert extractor_kind == "xlsx"

    def test_gid_fragment_is_ignored(self) -> None:
        url, _ = google_export_url(
            "https://docs.google.com/spreadsheets/d/1iXvNk2VQfiWQ_OlJzu0ZAj9mz4ui0IqkWLZbIpNvVsY/edit?gid=0#gid=0"
        )
        assert url == (
            "https://docs.google.com/spreadsheets/d/1iXvNk2VQfiWQ_OlJzu0ZAj9mz4ui0IqkWLZbIpNvVsY"
            "/export?format=xlsx"
        )


class TestUnsupported:
    def test_non_google_url_is_rejected(self) -> None:
        with pytest.raises(GoogleDocsUnsupported):
            google_export_url("https://example.com/document/d/abc/edit")

    def test_unrecognised_google_product_is_rejected(self) -> None:
        """Forms (docs.google.com/forms/...) are not documents we can export."""
        with pytest.raises(GoogleDocsUnsupported):
            google_export_url("https://docs.google.com/forms/d/abc/edit")

    def test_missing_id_is_rejected(self) -> None:
        with pytest.raises(GoogleDocsUnsupported):
            google_export_url("https://docs.google.com/document/d//edit")
