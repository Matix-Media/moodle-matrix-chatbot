"""Text extraction — see ``specs/006-extraction-chunking.md``.

Every extractor returns :class:`Segment`s carrying page/slide provenance, because a
citation of "Skript.pdf, S. 4" is far more useful than "somewhere in Skript.pdf".

Dispatch is by magic bytes first and extension second: the live corpus contains 155
files whose only name is ``index.html`` and whose declared mimetype is absent, and
real course folders reliably contain at least one mislabelled file.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable
from typing import Protocol

import structlog

from bsbot.shared.chunk import Segment

log = structlog.get_logger(__name__)

#: A PDF page yielding fewer characters than this is treated as a scan (AC-10).
OCR_CHAR_THRESHOLD = 80

#: Bumped whenever extraction behaviour changes; forces re-extraction from cached
#: blobs without re-downloading anything.
#:   1 -> initial extractors
#:   2 -> OCR fallback for scanned PDFs (70 scanned exam papers in the live corpus)
#:   3 -> .sql/.xls extractors; standalone image OCR (60 image attachments)
EXTRACT_VERSION = 3

OcrCallable = Callable[..., str]


class ExtractionError(RuntimeError):
    """The file could not be turned into text."""


class Extractor(Protocol):
    def __call__(self, data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]: ...


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


def extract_pdf(
    data: bytes,
    *,
    ocr: OcrCallable | None = None,
    ocr_threshold: int = OCR_CHAR_THRESHOLD,
) -> list[Segment]:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises a variety of types
        raise ExtractionError(f"cannot open PDF: {exc}") from exc

    segments: list[Segment] = []
    try:
        for index in range(1, doc.page_count + 1):
            page = doc.load_page(index - 1)
            try:
                text = page.get_text("text") or ""
            except Exception as exc:  # one bad page must not lose the document
                segments.append(
                    Segment(text="", page=index, meta={"skip_reason": f"page-error: {exc}"})
                )
                continue

            if len(text.strip()) >= ocr_threshold:
                segments.append(Segment(text=text, page=index))
                continue

            # Sparse page: probably a scan (AC-10).
            if ocr is None:
                segments.append(
                    Segment(
                        text=text, page=index, meta={"needs_ocr": True, "skip_reason": "needs-ocr"}
                    )
                )
                continue
            try:
                image = page.get_pixmap(dpi=150).tobytes("png")
                recovered = ocr(image, page=index)
            except Exception as exc:  # AC-12: never lose the file over one page
                log.warning("extract.ocr.failed", page=index, error=str(exc))
                segments.append(Segment(text=text, page=index, meta={"skip_reason": "ocr-failed"}))
                continue
            segments.append(Segment(text=(recovered or text), page=index, meta={"ocr": True}))
    finally:
        doc.close()
    return segments


# --------------------------------------------------------------------------- #
# Office
# --------------------------------------------------------------------------- #


def extract_docx(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"cannot open DOCX: {exc}") from exc

    parts = [p.text for p in document.paragraphs if p.text.strip()]
    # AC-4: timetables and grading schemes live in tables, not paragraphs.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return [Segment(text="\n".join(parts))] if parts else []


def extract_pptx(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    import pptx

    try:
        presentation = pptx.Presentation(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"cannot open PPTX: {exc}") from exc

    segments: list[Segment] = []
    for index, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
        notes = slide.notes_slide if slide.has_notes_slide else None
        if notes is not None and notes.notes_text_frame.text.strip():
            parts.append(f"Notizen: {notes.notes_text_frame.text}")
        if parts:
            segments.append(Segment(text="\n".join(parts), page=index, label=f"Folie {index}"))
    return segments


def extract_xlsx(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ExtractionError(f"cannot open XLSX: {exc}") from exc

    segments: list[Segment] = []
    try:
        for index, sheet in enumerate(workbook.worksheets, start=1):
            rows: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                segments.append(Segment(text="\n".join(rows), page=index, label=sheet.title))
    finally:
        workbook.close()
    return segments


def extract_xls(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    """Legacy binary Excel (.xls). ``xlrd`` 2.x reads this format only — xlsx support
    was deliberately dropped upstream, which is exactly the coverage gap openpyxl
    (used for .xlsx above) leaves (AC-21). No pandas: one extra dependency for a
    single file in the live corpus was not worth it.
    """
    import xlrd

    try:
        workbook = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise ExtractionError(f"cannot open XLS: {exc}") from exc

    segments: list[Segment] = []
    for index in range(workbook.nsheets):
        sheet = workbook.sheet_by_index(index)
        rows: list[str] = []
        for r in range(sheet.nrows):
            cells = [str(v).strip() for v in sheet.row_values(r) if str(v).strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            segments.append(Segment(text="\n".join(rows), page=index + 1, label=sheet.name))
    return segments


# --------------------------------------------------------------------------- #
# HTML and plain text
# --------------------------------------------------------------------------- #

_DROP_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "svg")


def extract_html(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    import base64

    from selectolax.parser import HTMLParser

    markup = _decode(data)
    try:
        tree = HTMLParser(markup)
    except Exception as exc:
        raise ExtractionError(f"cannot parse HTML: {exc}") from exc

    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()

    # Replace <img> tags with readable alt labels or OCR transcription
    for img in tree.css("img"):
        alt = (img.attributes.get("alt") or "").strip()
        src = img.attributes.get("src") or ""
        ocr_text = ""

        if ocr is not None and src.startswith("data:image/"):
            try:
                header, b64data = src.split(",", 1)
                mime = header.split(";")[0].removeprefix("data:")
                img_bytes = base64.b64decode(b64data)
                if len(img_bytes) > 500:  # ignore tiny spacers/tracking icons
                    ocr_text = ocr(img_bytes, page=None, mime_type=mime)
            except Exception as exc:
                log.warning("extract.html_img_ocr_failed", error=str(exc))

        replacement_text = ocr_text.strip() if ocr_text else (f"[Bild: {alt}]" if alt else "[Bild]")
        try:
            # A plain string is inserted as a text node (auto-escaped), which is exactly
            # what we want here — no need to build and parse a throwaway <span> wrapper.
            img.replace_with(replacement_text)
        except Exception:
            img.decompose()

    body = tree.body or tree.root
    if body is None:
        return []
    text = body.text(separator="\n", strip=True)
    return [Segment(text=text)] if text.strip() else []


def extract_text(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    text = _decode(data)
    return [Segment(text=text)] if text.strip() else []


def extract_odt(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            content = archive.read("content.xml")
        root = ET.fromstring(content)
        texts: list[str] = []
        for elem in root.iter():
            if elem.tag.endswith(("}p", "}h", "p", "h")):
                t = "".join(elem.itertext()).strip()
                if t:
                    texts.append(t)
        return [Segment(text="\n\n".join(texts))] if texts else []
    except Exception as exc:
        raise ExtractionError(f"cannot open ODT: {exc}") from exc


def extract_url_file(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    text = _decode(data)
    for line in text.splitlines():
        if line.strip().startswith("URL="):
            return [Segment(text=line.strip()[4:].strip())]
    return [Segment(text=text.strip())] if text.strip() else []


#: Gemini's officially supported image input types (verified against the API
#: docs). SVG is deliberately excluded: it is vector/XML markup, not a raster
#: image, and would need a different (non-OCR) handling path entirely.
_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def _detect_image_mime_type(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def extract_image(data: bytes, *, ocr: OcrCallable | None = None) -> list[Segment]:
    """A standalone PNG/JPEG/WEBP attachment, transcribed exactly like a scanned
    PDF page — same OCR interface, same caller-side caching (AC-22). Without an
    ``ocr`` callable this yields nothing, matching how a scanned PDF page with no
    OCR available is left for a later ``--ocr --retry-empty`` run rather than
    failing.

    The detected mime type is passed through to the OCR call rather than assuming
    PNG (spec 006 AC-24): sending WEBP bytes declared as image/png would likely
    fail or silently misbehave.
    """
    if ocr is None:
        return []
    mime_type = _detect_image_mime_type(data) or "image/png"
    try:
        text = ocr(data, page=None, mime_type=mime_type)
    except Exception as exc:  # a rate-limited or broken OCR call must not lose the file
        log.warning("extract.image_ocr_failed", error=str(exc))
        return []
    return [Segment(text=text, meta={"ocr": True})] if text and text.strip() else []


def _decode(data: bytes) -> str:
    """Decode bytes tolerantly — German umlauts appear in legacy encodings (AC-8)."""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

_BY_EXTENSION: dict[str, Extractor] = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "pptx": extract_pptx,
    "xlsx": extract_xlsx,
    "xls": extract_xls,
    "odt": extract_odt,
    "html": extract_html,
    "htm": extract_html,
    "txt": extract_text,
    "md": extract_text,
    "sql": extract_text,
    "csv": extract_text,
    "url": extract_url_file,
    "png": extract_image,
    "jpg": extract_image,
    "jpeg": extract_image,
    "webp": extract_image,
}

_OOXML_MARKERS: list[tuple[str, Extractor]] = [
    ("word/document.xml", extract_docx),
    ("ppt/presentation.xml", extract_pptx),
    ("xl/workbook.xml", extract_xlsx),
    ("content.xml", extract_odt),
]

_HTML_RE = re.compile(rb"<\s*(!doctype\s+html|html|body|div|p|h[1-6])\b", re.I)


def extractor_for(filename: str, data: bytes) -> Extractor | None:
    """Pick an extractor from magic bytes first, then the extension (AC-7, AC-23)."""
    if data[:5] == b"%PDF-":
        return extract_pdf
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return extract_image
    if data[:3] == b"\xff\xd8\xff":
        return extract_image
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return extract_image
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
            for marker, extractor in _OOXML_MARKERS:
                if marker in names:
                    return extractor
        except zipfile.BadZipFile:
            pass

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in _BY_EXTENSION:
        return _BY_EXTENSION[extension]

    if _HTML_RE.search(data[:2048]):
        return extract_html
    return None


def extract(
    data: bytes,
    *,
    filename: str,
    ocr: OcrCallable | None = None,
    ocr_threshold: int = OCR_CHAR_THRESHOLD,
) -> list[Segment]:
    """Extract text segments, raising :class:`ExtractionError` on unsupported input."""
    extractor = extractor_for(filename, data)
    if extractor is None:
        raise ExtractionError(f"no extractor for {filename!r} ({len(data)} bytes)")
    if extractor is extract_pdf:
        return extract_pdf(data, ocr=ocr, ocr_threshold=ocr_threshold)
    return extractor(data, ocr=ocr)


__all__ = [
    "EXTRACT_VERSION",
    "OCR_CHAR_THRESHOLD",
    "ExtractionError",
    "Segment",
    "extract",
    "extractor_for",
]
