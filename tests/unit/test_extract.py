"""Verifies spec 006 AC-1..AC-12 — text extraction.

Fixtures are generated in-process rather than committed as binaries, so the tests
stay readable and no school material ends up in the repo.
"""

from __future__ import annotations

import io

import pytest

from bsbot.cron.extract import ExtractionError, extract, extractor_for


def make_pdf(pages: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for para in paragraphs:
        document.add_paragraph(para)
    if table:
        t = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                t.cell(r, c).text = cell
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def make_pptx(slides: list[tuple[str, str]]) -> bytes:
    import pptx

    presentation = pptx.Presentation()
    for title, body in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    buf = io.BytesIO()
    presentation.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_xls(sheets: dict[str, list[list[str]]]) -> bytes:
    """Legacy binary Excel (.xls), via the write-only counterpart to xlrd."""
    import xlwt

    wb = xlwt.Workbook()
    for name, rows in sheets.items():
        ws = wb.add_sheet(name)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                ws.write(r, c, value)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestPdf:
    def test_text_per_page_with_numbers(self) -> None:
        """AC-1 / AC-2: citations need page numbers."""
        segments = extract(make_pdf(["Erste Seite", "Zweite Seite"]), filename="skript.pdf")
        assert [s.page for s in segments] == [1, 2]
        assert "Erste Seite" in segments[0].text
        assert "Zweite Seite" in segments[1].text

    def test_deterministic(self) -> None:
        """AC-9: the content hash depends on this."""
        data = make_pdf(["Wiederholbar"])
        assert [s.text for s in extract(data, filename="a.pdf")] == [
            s.text for s in extract(data, filename="a.pdf")
        ]

    def test_empty_page_is_flagged_for_ocr(self) -> None:
        """AC-10: scanned handouts are common in Berufsschule material."""
        full_page = (
            "Diese Seite enthaelt echten Fliesstext ueber Netzwerke, Protokolle "
            "und Adressierung, wie auf einer normalen Skriptseite."
        )
        segments = extract(make_pdf([full_page, ""]), filename="scan.pdf")
        needs = [s for s in segments if s.meta.get("needs_ocr")]
        assert len(needs) == 1
        assert needs[0].page == 2

    def test_ocr_hook_fills_in_scanned_pages(self) -> None:
        """AC-11: injectable, so tests never call a paid API."""
        calls: list[int] = []

        def fake_ocr(image: bytes, *, page: int) -> str:
            calls.append(page)
            return f"OCR von Seite {page}"

        full_page = (
            "Diese Seite enthaelt echten Fliesstext ueber Netzwerke, Protokolle "
            "und Adressierung, wie auf einer normalen Skriptseite."
        )
        segments = extract(make_pdf([full_page, ""]), filename="scan.pdf", ocr=fake_ocr)
        assert calls == [2]
        assert "OCR von Seite 2" in segments[1].text
        assert not segments[1].meta.get("needs_ocr")

    def test_missing_ocr_skips_the_page_without_failing_the_file(self) -> None:
        """AC-12"""
        full_page = (
            "Diese Seite enthaelt echten Fliesstext ueber Netzwerke, Protokolle "
            "und Adressierung, wie auf einer normalen Skriptseite."
        )
        segments = extract(make_pdf([full_page, ""]), filename="scan.pdf", ocr=None)
        assert len(segments) == 2
        assert segments[1].meta["skip_reason"] == "needs-ocr"

    def test_failing_ocr_does_not_abort_the_document(self) -> None:
        """AC-12: a rate-limited OCR call must not lose the whole PDF."""

        def broken_ocr(image: bytes, *, page: int) -> str:
            raise RuntimeError("429 rate limited")

        full_page = (
            "Diese Seite enthaelt echten Fliesstext ueber Netzwerke, Protokolle "
            "und Adressierung, wie auf einer normalen Skriptseite."
        )
        segments = extract(make_pdf([full_page, ""]), filename="scan.pdf", ocr=broken_ocr)
        assert "Netzwerke" in segments[0].text
        assert segments[1].meta["skip_reason"] == "ocr-failed"


class TestOffice:
    def test_docx_includes_table_cells(self) -> None:
        """AC-4: timetables and grading schemes live in tables."""
        data = make_docx(["Einleitung"], table=[["Fach", "Note"], ["Mathe", "2"]])
        text = "\n".join(s.text for s in extract(data, filename="doku.docx"))
        assert "Einleitung" in text
        assert "Fach" in text and "Mathe" in text and "2" in text

    def test_pptx_one_segment_per_slide(self) -> None:
        """AC-3"""
        data = make_pptx([("Titel A", "Inhalt A"), ("Titel B", "Inhalt B")])
        segments = extract(data, filename="folien.pptx")
        assert len(segments) == 2
        assert [s.page for s in segments] == [1, 2]
        assert "Titel A" in segments[0].text and "Inhalt A" in segments[0].text

    def test_xlsx_one_segment_per_sheet(self) -> None:
        """AC-5"""
        data = make_xlsx({"Noten": [["Fach", "Note"], ["Mathe", "2"]], "Leer": [[]]})
        segments = extract(data, filename="tabelle.xlsx")
        labels = [s.label for s in segments]
        assert "Noten" in labels
        assert "Mathe" in "\n".join(s.text for s in segments)


class TestHtml:
    def test_scripts_and_styles_are_removed(self) -> None:
        """AC-6"""
        html = (
            b"<html><head><style>p{color:red}</style><script>alert(1)</script></head>"
            b"<body><nav>Navigation</nav><p>Die Klausur ist am 15.03.2026.</p></body></html>"
        )
        text = "\n".join(s.text for s in extract(html, filename="index.html"))
        assert "Die Klausur ist am 15.03.2026." in text
        assert "alert" not in text and "color:red" not in text

    def test_entities_are_decoded(self) -> None:
        html = b"<html><body><p>Gr&uuml;&szlig;e &amp; Danke</p></body></html>"
        text = "\n".join(s.text for s in extract(html, filename="index.html"))
        assert "Grüße & Danke" in text


class TestDispatch:
    def test_extension_selects_extractor(self) -> None:
        """AC-7"""
        assert extractor_for("a.pdf", b"") is extractor_for("B.PDF", b"")

    def test_magic_bytes_win_over_a_wrong_extension(self) -> None:
        """AC-7: real corpora contain mislabelled files."""
        segments = extract(make_pdf(["Trotzdem gelesen"]), filename="falsch.txt")
        assert "Trotzdem gelesen" in segments[0].text

    def test_extensionless_html_is_handled(self) -> None:
        """AC-7: Moodle page/book content arrives as bare 'index.html'."""
        segments = extract(b"<html><body><p>Hallo</p></body></html>", filename="index.html")
        assert "Hallo" in segments[0].text

    def test_plain_text_passes_through(self) -> None:
        assert "Notiz" in extract(b"Notiz", filename="a.txt")[0].text

    def test_latin1_text_does_not_crash(self) -> None:
        """AC-8: German umlauts in legacy encodings are common."""
        assert extract("Grüße".encode("latin-1"), filename="a.txt")[0].text

    def test_unsupported_type_raises_a_recorded_error(self) -> None:
        """AC-8"""
        with pytest.raises(ExtractionError):
            extract(b"\x00\x01binary", filename="archiv.zip")

    def test_corrupt_pdf_raises_extraction_error_not_a_crash(self) -> None:
        """AC-8: one bad file must not abort the batch."""
        with pytest.raises(ExtractionError):
            extract(b"%PDF-1.4 truncated garbage", filename="kaputt.pdf")


class TestXls:
    def test_one_segment_per_sheet(self) -> None:
        """AC-21: legacy binary Excel, same shape as .xlsx without pandas."""
        data = make_xls({"Noten": [["Fach", "Note"], ["Mathe", "2"]], "Leer": [[]]})
        segments = extract(data, filename="tabelle.xls")
        labels = [s.label for s in segments]
        assert "Noten" in labels
        assert "Mathe" in "\n".join(s.text for s in segments)

    def test_empty_workbook_yields_nothing(self) -> None:
        data = make_xls({"Leer": [[]]})
        assert extract(data, filename="leer.xls") == []


class TestSql:
    def test_sql_is_extracted_as_plain_text(self) -> None:
        """AC-20"""
        sql = (
            b"CREATE TABLE Tierheim (id INT, name TEXT);\nINSERT INTO Tierheim VALUES (1, 'Bello');"
        )
        segments = extract(sql, filename="01_create_db.sql")
        assert "CREATE TABLE Tierheim" in segments[0].text
        assert "Bello" in segments[0].text


class TestImage:
    def test_without_ocr_yields_nothing(self) -> None:
        """AC-22: matches the existing scanned-PDF-without-OCR behaviour."""
        png = bytes.fromhex("89504e470d0a1a0a") + b"restofpngbytes"
        assert extract(png, filename="foto.png", ocr=None) == []

    def test_with_ocr_transcribes_the_image(self) -> None:
        """AC-22"""
        png = bytes.fromhex("89504e470d0a1a0a") + b"restofpngbytes"

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            assert image == png
            assert mime_type == "image/png"
            return "Handschriftliche Notiz: Abgabe bis Freitag."

        segments = extract(png, filename="foto.png", ocr=fake_ocr)
        assert "Abgabe bis Freitag" in segments[0].text

    def test_ocr_failure_yields_nothing_not_a_crash(self) -> None:
        """AC-22: mirrors the PDF ocr-failed path — never abort the batch."""
        png = bytes.fromhex("89504e470d0a1a0a") + b"restofpngbytes"

        def broken_ocr(
            image: bytes, *, page: int | None = None, mime_type: str = "image/png"
        ) -> str:
            raise RuntimeError("429 rate limited")

        assert extract(png, filename="foto.png", ocr=broken_ocr) == []

    def test_png_magic_bytes_are_recognised_regardless_of_extension(self) -> None:
        """AC-23"""
        png = bytes.fromhex("89504e470d0a1a0a") + b"restofpngbytes"

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            return "Text im Bild."

        segments = extract(png, filename="attachment.dat", ocr=fake_ocr)
        assert "Text im Bild" in segments[0].text

    def test_jpeg_magic_bytes_are_recognised(self) -> None:
        """AC-23"""
        jpeg = bytes.fromhex("ffd8ffe0") + b"restofjpegbytes"

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            return "JPEG Inhalt."

        segments = extract(jpeg, filename="scan.jpeg", ocr=fake_ocr)
        assert "JPEG Inhalt" in segments[0].text


class TestWebp:
    """Gemini officially supports image/webp as an input type (verified against
    the API docs) — the only reason WEBP wasn't included initially was scope, not
    a technical limitation."""

    def test_without_ocr_yields_nothing(self) -> None:
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"restofwebpbytes"
        assert extract(webp, filename="foto.webp", ocr=None) == []

    def test_with_ocr_transcribes_the_image(self) -> None:
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"restofwebpbytes"

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            assert mime_type == "image/webp", f"got {mime_type!r}"
            return "Screenshot-Text."

        segments = extract(webp, filename="foto.webp", ocr=fake_ocr)
        assert "Screenshot-Text" in segments[0].text

    def test_webp_magic_bytes_are_recognised_regardless_of_extension(self) -> None:
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"restofwebpbytes"
        segments = extract(
            webp,
            filename="attachment.dat",
            ocr=lambda image, **kw: "Erkannt trotz falscher Endung.",
        )
        assert "Erkannt trotz falscher Endung" in segments[0].text


class TestOcrMimeTypeIsCorrectPerFormat:
    """Regression: the OCR call must declare the *actual* image format to Gemini,
    not a hardcoded 'image/png' — sending WEBP bytes mislabelled as PNG would
    likely fail or silently misbehave."""

    def test_png_declares_png(self) -> None:
        png = bytes.fromhex("89504e470d0a1a0a") + b"rest"
        seen = {}

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            seen["mime_type"] = mime_type
            return "x"

        extract(png, filename="a.png", ocr=fake_ocr)
        assert seen["mime_type"] == "image/png"

    def test_jpeg_declares_jpeg(self) -> None:
        jpeg = bytes.fromhex("ffd8ffe0") + b"rest"
        seen = {}

        def fake_ocr(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            seen["mime_type"] = mime_type
            return "x"

        extract(jpeg, filename="a.jpeg", ocr=fake_ocr)
        assert seen["mime_type"] == "image/jpeg"
