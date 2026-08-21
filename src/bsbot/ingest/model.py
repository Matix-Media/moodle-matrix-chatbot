"""The content model — see ``specs/003-content-model.md``.

A :class:`ContentItem` is the atomic text-bearing unit of the corpus. Everything
downstream (fetching, extraction, chunking, citation) is expressed in terms of it,
so it must carry enough provenance to link an answer back to Moodle.
"""

from __future__ import annotations

import enum
import html
import re

from pydantic import BaseModel

#: MIME prefixes and extensions we will never get text out of. They stay in the
#: manifest for completeness but never enter the text corpus (spec 003 AC-13).
BINARY_MIME_PREFIXES = ("image/", "video/", "audio/")
#: PNG/JPEG/WEBP get an explicit carve-out from BINARY_MIME_PREFIXES/BINARY_EXTENSIONS
#: below (spec 006 AC-22, AC-24): they get an OCR-style fallback exactly like a
#: scanned PDF page (Gemini supports exactly these three as image inputs), so
#: excluding them outright would mean 63 image attachments in the live corpus
#: never even reach extraction. Kept as a narrow exemption rather than dropping
#: "image/" from BINARY_MIME_PREFIXES entirely, which would incorrectly wave
#: through other image types (TIFF, GIF, ...) we have no extractor for. SVG is
#: deliberately excluded even though it's an image — vector/XML markup, not a
#: raster image, and would need a different (non-OCR) handling path entirely.
OCR_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
OCR_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})
BINARY_EXTENSIONS = frozenset(
    {
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "exe",
        "msi",
        "dmg",
        "iso",
        "mp3",
        "mp4",
        "mov",
        "avi",
        "gif",
        "svg",
        "bmp",
        "tif",
        "tiff",
        "psd",
        "ai",
    }
)

#: Extensions we know how to extract text from (spec 004).
TEXT_EXTENSIONS = frozenset(
    {
        "pdf",
        "docx",
        "doc",
        "pptx",
        "ppt",
        "xlsx",
        "xls",
        "odt",
        "odp",
        "ods",
        "html",
        "htm",
        "txt",
        "md",
        "csv",
        "rtf",
        "sql",
        "png",
        "jpg",
        "jpeg",
        "webp",
    }
)


class ContentKind(enum.StrEnum):
    """Where an item's text comes from, which decides how it is fetched."""

    INLINE = "inline"  # already have the text (label/description HTML)
    HTML = "html"  # generated Moodle HTML (page, book chapter) — fetch then parse
    FILE = "file"  # a real attachment — download then extract
    EXTERNAL = "external"  # a link off-site (Nextcloud share, Google Docs, ...)


class FileRef(BaseModel):
    """A downloadable file, as Moodle describes it."""

    model_config = {"frozen": True}

    url: str
    filename: str
    filesize: int = 0
    mimetype: str | None = None
    timemodified: int = 0

    @property
    def extension(self) -> str:
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""


class ContentItem(BaseModel):
    """One text-bearing unit of a course."""

    doc_id: str
    course_id: int
    course_name: str
    section_name: str
    module_id: int
    module_name: str
    modname: str
    title: str
    kind: ContentKind
    header_path: list[str]
    timemodified: int = 0

    module_url: str | None = None
    text: str | None = None
    file: FileRef | None = None
    external_url: str | None = None

    extractable: bool = True
    skip_reason: str | None = None

    @property
    def header_text(self) -> str:
        """Breadcrumb used as embedding context and as the citation label."""
        return " › ".join(self.header_path)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")
_BLOCK_RE = re.compile(r"</(p|div|li|h[1-6]|tr|table|blockquote)>|<br\s*/?>", re.I)


def html_to_text(markup: str | None) -> str:
    """Strip HTML to readable plain text, preserving block-level line breaks.

    Deliberately small: Moodle descriptions are simple fragments, and pulling in a
    full parser here would buy nothing. Real documents go through spec 004's
    extractors instead.
    """
    if not markup:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()


#: Titles longer than this are truncated. Moodle derives label module names from
#: their body text, so they can run to hundreds of characters.
MAX_TITLE_CHARS = 80


def clean_title(raw: str | None, fallback: str = "") -> str:
    """Collapse a Moodle title to a single tidy line.

    Label module names arrive containing newlines and runs of spaces; used raw they
    corrupt both the citation breadcrumb and the embedding context prefix.
    """
    text = " ".join((raw or "").split()) or fallback
    if len(text) > MAX_TITLE_CHARS:
        text = text[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return text


def classify_file(ref: FileRef, max_bytes: int) -> tuple[bool, str | None]:
    """Decide whether a file should enter the text corpus.

    Returns ``(extractable, skip_reason)``.

    The zero-byte exemption matters: Moodle reports generated ``page`` and ``book``
    HTML as ``filesize: 0``, and on the live site that is 155 of 515 files — most of
    the prose. A naive size filter would silently discard it (spec 003 AC-14).
    """
    extension = ref.extension
    mimetype = (ref.mimetype or "").lower()

    is_ocr_image = extension in OCR_IMAGE_EXTENSIONS or mimetype in OCR_IMAGE_MIME_TYPES
    if not is_ocr_image:
        if extension in BINARY_EXTENSIONS or mimetype.startswith(BINARY_MIME_PREFIXES):
            return False, "binary-type"
        if extension and extension not in TEXT_EXTENSIONS and not mimetype:
            return False, "unknown-type"
    if ref.filesize > max_bytes > 0:
        return False, "too-large"
    return True, None
