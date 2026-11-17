"""Render one small markup into every file format the app ingests.

Course material is written once in a Markdown subset and turned into PDF, DOCX, ODT,
PPTX, HTML, Markdown and plain text here; spreadsheets and images have their own
builders. Only what the generator needs is supported:

    # / ## / ### heading        - bullet          1. numbered
    | a | b |  table rows       ``` code fence     blank line: next paragraph
    **bold** inline             > note (rendered as an indented remark)
    ---  page break (PDF/DOCX)  ## Folie  starts a new slide (PPTX)
    Notiz: ...  speaker notes of the current slide (PPTX)

Everything is deterministic: the same input gives byte-for-byte the same text, and
only the timestamps some container formats embed differ between runs.
"""

from __future__ import annotations

import html
import io
import random
import re
import textwrap
import zipfile
from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["h", "p", "ul", "ol", "table", "code", "note", "break"]


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")


def parse(markup: str) -> list[Block]:
    """The markup above as a flat list of blocks."""
    blocks: list[Block] = []
    lines = textwrap.dedent(markup).strip("\n").splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("p", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("```"):
            flush()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", textwrap.dedent("\n".join(code))))
        elif stripped == "---":
            flush()
            blocks.append(Block("break"))
        elif m := re.match(r"^(#{1,3})\s+(.*)$", stripped):
            flush()
            blocks.append(Block("h", m.group(2), level=len(m.group(1))))
        elif stripped.startswith("|"):
            flush()
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(Block("table", rows=rows))
            continue
        elif re.match(r"^[-*]\s+", stripped):
            flush()
            items: list[str] = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]).strip())
                i += 1
            blocks.append(Block("ul", items=items))
            continue
        elif re.match(r"^\d+\.\s+", stripped):
            flush()
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip())
                i += 1
            blocks.append(Block("ol", items=items))
            continue
        elif stripped.startswith(">"):
            flush()
            blocks.append(Block("note", stripped.lstrip("> ").strip()))
        else:
            para.append(stripped)
        i += 1
    flush()
    return blocks


def plain(text: str) -> str:
    return _INLINE_BOLD.sub(r"\1", text)


def _inline_html(text: str) -> str:
    return _INLINE_BOLD.sub(r"<strong>\1</strong>", html.escape(text, quote=False))


# --------------------------------------------------------------------------- #
# HTML, Markdown, text
# --------------------------------------------------------------------------- #


def to_html_fragment(markup: str) -> str:
    """Moodle-editor-style HTML (pages, books, labels, intros)."""
    out: list[str] = []
    for b in parse(markup):
        if b.kind == "h":
            level = min(b.level + 2, 6)  # Moodle content starts below the page title
            out.append(f"<h{level}>{_inline_html(b.text)}</h{level}>")
        elif b.kind == "p":
            out.append(f"<p>{_inline_html(b.text)}</p>")
        elif b.kind in ("ul", "ol"):
            tag = b.kind
            out.append(f"<{tag}>" + "".join(f"<li>{_inline_html(x)}</li>" for x in b.items))
            out[-1] += f"</{tag}>"
        elif b.kind == "table":
            head, *body = b.rows
            rows = ["<tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in head) + "</tr>"]
            rows += [
                "<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>" for r in body
            ]
            out.append('<table class="table table-striped">' + "".join(rows) + "</table>")
        elif b.kind == "code":
            out.append(f"<pre><code>{html.escape(b.text)}</code></pre>")
        elif b.kind == "note":
            out.append(f'<div class="alert alert-info"><p>{_inline_html(b.text)}</p></div>')
    return "\n".join(out)


def to_html_document(title: str, markup: str, *, lang: str = "de") -> bytes:
    """A standalone HTML file, with the navigation chrome the extractor drops."""
    body = to_html_fragment(markup)
    page = f"""<!DOCTYPE html>
<html lang="{lang}">
<head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:sans-serif;max-width:50em;margin:auto}}</style>
<script>window.dataLayer = window.dataLayer || [];</script></head>
<body>
<nav><a href="index.html">Start</a> | <a href="impressum.html">Impressum</a></nav>
<h1>{html.escape(title)}</h1>
{body}
<footer>Stand: November 2026</footer>
</body></html>
"""
    return page.encode("utf-8")


def to_markdown(title: str, markup: str) -> bytes:
    text = f"# {title}\n\n" + textwrap.dedent(markup).strip() + "\n"
    return text.replace("\n---\n", "\n").encode("utf-8")


def to_text(title: str, markup: str, *, encoding: str = "utf-8") -> bytes:
    out = [title, "=" * len(title), ""]
    for b in parse(markup):
        if b.kind == "h":
            out += [plain(b.text).upper() if b.level == 1 else plain(b.text), ""]
        elif b.kind in ("p", "note"):
            out += [textwrap.fill(plain(b.text), 78), ""]
        elif b.kind in ("ul", "ol"):
            for n, item in enumerate(b.items, 1):
                bullet = f"{n}." if b.kind == "ol" else "*"
                out.append(
                    textwrap.fill(
                        plain(item), 78, initial_indent=f"  {bullet} ", subsequent_indent="    "
                    )
                )
            out.append("")
        elif b.kind == "table":
            widths = [
                max(len(plain(r[c])) for r in b.rows if c < len(r)) for c in range(len(b.rows[0]))
            ]
            for r in b.rows:
                out.append("  ".join(plain(c).ljust(w) for c, w in zip(r, widths, strict=False)))
            out.append("")
        elif b.kind == "code":
            out += [textwrap.indent(b.text, "    "), ""]
    return ("\r\n".join(out) if encoding == "cp1252" else "\n".join(out)).encode(encoding)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

_PDF_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 18pt; color: #1d3557; margin-bottom: 6pt; }
h2 { font-size: 13.5pt; color: #1d3557; margin-top: 12pt; }
h3 { font-size: 11.5pt; margin-top: 8pt; }
table { border-collapse: collapse; margin: 6pt 0; }
td, th { border: 1px solid #888; padding: 2pt 4pt; font-size: 9.5pt; }
th { background-color: #e5e9f0; }
pre { font-family: monospace; font-size: 9pt; background-color: #f3f3f3; padding: 4pt; }
.note { border-left: 3px solid #e63946; padding-left: 6pt; font-style: italic; }
"""


def _pdf_html(title: str, markup: str) -> list[str]:
    """HTML per PDF page group (``---`` forces a page break)."""
    pages: list[list[str]] = [[f"<h1>{html.escape(title)}</h1>"]]
    for b in parse(markup):
        if b.kind == "break":
            pages.append([])
        elif b.kind == "h":
            pages[-1].append(f"<h{b.level + 1}>{_inline_html(b.text)}</h{b.level + 1}>")
        elif b.kind == "note":
            pages[-1].append(f'<p class="note">{_inline_html(b.text)}</p>')
        else:
            pages[-1].append(to_html_fragment_block(b))
    return ["\n".join(p) for p in pages]


def to_html_fragment_block(b: Block) -> str:
    if b.kind == "p":
        return f"<p>{_inline_html(b.text)}</p>"
    if b.kind in ("ul", "ol"):
        return (
            f"<{b.kind}>" + "".join(f"<li>{_inline_html(x)}</li>" for x in b.items) + f"</{b.kind}>"
        )
    if b.kind == "table":
        head, *body = b.rows
        rows = ["<tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in head) + "</tr>"]
        rows += ["<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>" for r in body]
        return "<table>" + "".join(rows) + "</table>"
    if b.kind == "code":
        return f"<pre>{html.escape(b.text)}</pre>"
    return ""


def to_pdf(title: str, markup: str, *, header: str = "", author: str = "") -> bytes:
    """A text PDF with running header and page numbers."""
    import pymupdf

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.paper_rect("a4")
    where = pymupdf.Rect(56, 64, mediabox.width - 56, mediabox.height - 64)
    for chunk in _pdf_html(title, markup):
        story = pymupdf.Story(html=chunk, user_css=_PDF_CSS)
        more = True
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    writer.close()

    doc = pymupdf.open(stream=buffer.getvalue(), filetype="pdf")
    total = doc.page_count
    for number, page in enumerate(doc, start=1):
        if header:
            page.insert_text((56, 40), header, fontsize=8, color=(0.4, 0.4, 0.4))
        page.insert_text(
            (56, mediabox.height - 32),
            f"Seite {number} von {total}",
            fontsize=8,
            color=(0.4, 0.4, 0.4),
        )
    doc.set_metadata(
        {"title": title, "author": author, "creator": "LibreOffice Writer", "producer": "pymupdf"}
    )
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return bytes(data)


def to_scanned_pdf(title: str, markup: str, *, seed: int, header: str = "") -> bytes:
    """An image-only PDF that looks photocopied: no text layer, so the API needs OCR."""
    import pymupdf
    from PIL import Image, ImageFilter

    rng = random.Random(seed)
    source = pymupdf.open(stream=to_pdf(title, markup, header=header), filetype="pdf")
    out = pymupdf.open()
    for page in source:
        pix = page.get_pixmap(dpi=120, colorspace=pymupdf.csGRAY)
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        image = image.rotate(rng.uniform(-1.4, 1.4), fillcolor=235, expand=False)
        image = image.filter(ImageFilter.GaussianBlur(0.6))
        noise = image.load()
        assert noise is not None
        for _ in range(image.width * image.height // 180):
            x, y = rng.randrange(image.width), rng.randrange(image.height)
            noise[x, y] = rng.choice((120, 160, 200))
        jpeg = io.BytesIO()
        image.save(jpeg, "JPEG", quality=55)
        target = out.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, stream=jpeg.getvalue())
    out.set_metadata({"title": "", "creator": "Canon iR-ADV C5535 Scan", "producer": "Canon"})
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    source.close()
    return bytes(data)


# --------------------------------------------------------------------------- #
# DOCX and ODT
# --------------------------------------------------------------------------- #


def to_docx(title: str, markup: str, *, author: str = "") -> bytes:
    import docx
    from docx.shared import Pt

    document = docx.Document()
    document.core_properties.title = title
    document.core_properties.author = author
    document.add_heading(title, level=0)
    for b in parse(markup):
        if b.kind == "h":
            document.add_heading(plain(b.text), level=min(b.level, 3))
        elif b.kind in ("p", "note"):
            paragraph = document.add_paragraph()
            for n, part in enumerate(_INLINE_BOLD.split(b.text)):
                run = paragraph.add_run(part)
                run.bold = n % 2 == 1
                run.italic = b.kind == "note"
        elif b.kind in ("ul", "ol"):
            style = "List Bullet" if b.kind == "ul" else "List Number"
            for item in b.items:
                document.add_paragraph(plain(item), style=style)
        elif b.kind == "table":
            table = document.add_table(rows=0, cols=len(b.rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(b.rows):
                cells = table.add_row().cells
                for c, value in enumerate(row[: len(cells)]):
                    cells[c].text = plain(value)
                    if r == 0:
                        for run in cells[c].paragraphs[0].runs:
                            run.bold = True
        elif b.kind == "code":
            paragraph = document.add_paragraph()
            run = paragraph.add_run(b.text)
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif b.kind == "break":
            document.add_page_break()
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_ODT_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.3"'
)


def to_odt(title: str, markup: str, *, author: str = "") -> bytes:
    """A minimal but valid OpenDocument text (LibreOffice opens it)."""

    def esc(s: str) -> str:
        return html.escape(plain(s), quote=False)

    body = [f'<text:h text:outline-level="1">{esc(title)}</text:h>']
    for b in parse(markup):
        if b.kind == "h":
            body.append(f'<text:h text:outline-level="{b.level + 1}">{esc(b.text)}</text:h>')
        elif b.kind in ("p", "note"):
            body.append(f"<text:p>{esc(b.text)}</text:p>")
        elif b.kind in ("ul", "ol"):
            items = "".join(
                f"<text:list-item><text:p>{esc(x)}</text:p></text:list-item>" for x in b.items
            )
            body.append(f"<text:list>{items}</text:list>")
        elif b.kind == "table":
            cols = len(b.rows[0])
            rows = "".join(
                "<table:table-row>"
                + "".join(
                    f'<table:table-cell office:value-type="string"><text:p>{esc(c)}</text:p>'
                    "</table:table-cell>"
                    for c in r
                )
                + "</table:table-row>"
                for r in b.rows
            )
            body.append(
                f'<table:table table:name="Tabelle{len(body)}">'
                f'<table:table-column table:number-columns-repeated="{cols}"/>{rows}'
                "</table:table>"
            )
        elif b.kind == "code":
            body += [
                f"<text:p>{html.escape(line, quote=False)}</text:p>" for line in b.text.splitlines()
            ]
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_ODT_NS}>'
        f"<office:body><office:text>{''.join(body)}</office:text></office:body>"
        "</office:document-content>"
    )
    meta = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-meta {_ODT_NS}>'
        f"<office:meta><dc:title>{esc(title)}</dc:title><dc:creator>{esc(author)}</dc:creator>"
        "<meta:generator>LibreOffice/24.8</meta:generator></office:meta>"
        "</office:document-meta>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.3">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype", (2026, 11, 2, 8, 0, 0)),
            "application/vnd.oasis.opendocument.text",
            zipfile.ZIP_STORED,
        )
        for name, data in (
            ("content.xml", content),
            ("meta.xml", meta),
            ("META-INF/manifest.xml", manifest),
        ):
            archive.writestr(
                zipfile.ZipInfo(name, (2026, 11, 2, 8, 0, 0)), data, zipfile.ZIP_DEFLATED
            )
    return buffer.getvalue()


def to_rtf(title: str, markup: str) -> bytes:
    """Old-style RTF: the plugin accepts it, the API has no extractor for it."""

    def esc(s: str) -> str:
        return "".join(c if ord(c) < 128 else f"\\u{ord(c)}?" for c in plain(s))

    lines = [r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}\f0\fs22", rf"{{\b\fs32 {esc(title)}\par}}"]
    for b in parse(markup):
        if b.kind in ("h", "p", "note"):
            lines.append(
                (r"{\b " + esc(b.text) + r"}\par") if b.kind == "h" else esc(b.text) + r"\par"
            )
        elif b.kind in ("ul", "ol"):
            lines += [r"\bullet  " + esc(x) + r"\par" for x in b.items]
        elif b.kind == "table":
            lines += [esc(" | ".join(r)) + r"\par" for r in b.rows]
    return ("\n".join(lines) + "}").encode("ascii")


# --------------------------------------------------------------------------- #
# PPTX
# --------------------------------------------------------------------------- #


def to_pptx(title: str, markup: str, *, subtitle: str = "") -> bytes:
    """Slides: every ``#``/``##`` heading starts one; ``Notiz:`` lines are speaker notes."""
    import pptx
    from pptx.util import Inches, Pt

    deck = pptx.Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    cover = deck.slides.add_slide(deck.slide_layouts[0])
    cover.shapes.title.text = title
    cover.placeholders[1].text = subtitle

    slides: list[tuple[str, list[Block], list[str]]] = []
    for b in parse(markup):
        if b.kind == "h":
            slides.append((plain(b.text), [], []))
        elif not slides:
            continue
        elif b.kind == "p" and b.text.startswith("Notiz:"):
            slides[-1][2].append(b.text.removeprefix("Notiz:").strip())
        else:
            slides[-1][1].append(b)

    for heading, blocks, notes in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[5])  # title only
        slide.shapes.title.text = heading
        top = Inches(1.6)
        bullets: list[tuple[str, int]] = []
        for b in blocks:
            if b.kind in ("ul", "ol"):
                bullets += [(plain(x), 0) for x in b.items]
            elif b.kind in ("p", "note"):
                bullets.append((plain(b.text), 0))
            elif b.kind == "code":
                bullets += [(line, 1) for line in b.text.splitlines()]
        if bullets:
            box = slide.shapes.add_textbox(Inches(0.7), top, Inches(12), Inches(4.8))
            frame = box.text_frame
            frame.word_wrap = True
            for n, (text, level) in enumerate(bullets):
                paragraph = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
                paragraph.text = ("• " if level == 0 else "    ") + text
                paragraph.font.size = Pt(20 if level == 0 else 14)
            top += Inches(0.45) * min(len(bullets), 9)
        for b in blocks:
            if b.kind == "table":
                shape = slide.shapes.add_table(
                    len(b.rows),
                    len(b.rows[0]),
                    Inches(0.7),
                    top,
                    Inches(12),
                    Inches(0.4) * len(b.rows),
                )
                for r, row in enumerate(b.rows):
                    for c, value in enumerate(row):
                        cell = shape.table.cell(r, c)
                        cell.text = plain(value)
                        cell.text_frame.paragraphs[0].font.size = Pt(14)
        if notes:
            slide.notes_slide.notes_text_frame.text = "\n".join(notes)
    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Spreadsheets
# --------------------------------------------------------------------------- #

Sheet = tuple[str, list[list[object]]]


def to_xlsx(sheets: list[Sheet], *, title: str = "") -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = title
    for name, rows in sheets:
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDE5F0")
        for column in sheet.columns:
            width = max(len(str(c.value or "")) for c in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 8), 60)
        sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def to_xls(sheets: list[Sheet]) -> bytes:
    """Legacy binary Excel 97 (the API reads it with xlrd)."""
    import xlwt

    workbook = xlwt.Workbook(encoding="utf-8")
    bold = xlwt.easyxf("font: bold on; pattern: pattern solid, fore_colour pale_blue")
    for name, rows in sheets:
        sheet = workbook.add_sheet(name)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                sheet.write(r, c, value, bold) if r == 0 else sheet.write(r, c, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def to_csv(rows: list[list[object]], *, delimiter: str = ";") -> bytes:
    """German Excel-style CSV: semicolons, decimal commas, UTF-8 with BOM."""
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    for row in rows:
        writer.writerow([str(v).replace(".", ",") if isinstance(v, float) else v for v in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")
