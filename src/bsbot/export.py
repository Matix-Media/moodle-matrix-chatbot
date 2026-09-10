"""Export all Moodle courses, activities, and documents to Markdown and combined text.

Exports:
1. An individual, structured Markdown file for every single document/item in the Moodle space.
2. A master combined file containing the complete text of all content across all courses.
3. Chunks split cleanly along document boundaries into ~800,000 token parts.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import markdownify
import structlog
import tiktoken

from bsbot.index.store import Store
from bsbot.ingest.extract import extract
from bsbot.pii.tokenizer import PiiTokenizer

log = structlog.get_logger(__name__)

_INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|]')
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_filename(name: str, max_len: int = 60) -> str:
    """Sanitize a string to be a safe filesystem filename component."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(". ")
    if not cleaned:
        cleaned = "unnamed"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned


def format_timestamp(ts: int | None) -> str:
    if not ts:
        return "Unknown"
    try:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def clean_image_placeholders(text: str, source_url: str = "") -> str:
    """Replace raw base64 data URIs and embedded images with informative placeholders."""

    def _repl(m: re.Match[str]) -> str:
        alt = m.group(1).strip() if m.group(1) else "Abbildung"
        if source_url:
            return f"[Bild: {alt} | Quelle: {source_url}]"
        return f"[Bild: {alt}]"

    text = re.sub(r'!\[([^\]]*)\]\(data:image/[^;]+;base64,[^"\'\)\s>]+\)', _repl, text)
    text = re.sub(r'data:image/[^;]+;base64,[^"\'\)\s>]+', "[Eingebettetes Bild]", text)
    return text


def extract_document_markdown(
    doc: dict[str, Any],
    store: Store,
    blobs_dir: Path,
) -> tuple[str, str]:
    """Extract markdown content and plain text content for a document.

    Returns:
        (markdown_content, plain_text_content)
    """
    text = doc.get("text")
    filename = doc.get("filename") or "unknown"
    blob_sha = doc.get("blob_sha256")
    file_url = doc.get("file_url")
    external_url = doc.get("external_url")
    module_url = doc.get("module_url") or ""
    doc_id = doc.get("doc_id", "")
    source_link = file_url or module_url or external_url or ""

    # Check for AI-extracted OCR / description cached in store
    target_sha = blob_sha
    if not target_sha and file_url:
        row = store.connection.execute(
            "SELECT sha256 FROM fetches WHERE url = ?", (file_url,)
        ).fetchone()
        if row and row[0]:
            target_sha = row[0]

    ai_meaning: str | None = None
    if target_sha:
        for tag in ("describe-v1", "ocr-v1"):
            cached = store.cached_ocr(target_sha, tag)
            if cached and cached.strip():
                ai_meaning = cached.strip()
                break

    # 1. Direct text available (inline labels, descriptions, forum topics, etc.)
    if text and text.strip():
        if "<" in text and ">" in text:
            md_body = markdownify.markdownify(text, heading_style="ATX").strip()
        else:
            md_body = text.strip()
        cleaned_body = clean_image_placeholders(md_body, source_url=source_link)
        return cleaned_body, cleaned_body

    # 2. Blob available on disk
    if target_sha:
        blob_path = blobs_dir / target_sha[:2] / target_sha
        if blob_path.exists():
            data = blob_path.read_bytes()
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            # If it's a standalone image file
            if ext in ("png", "jpg", "jpeg", "webp"):
                if ai_meaning:
                    md = (
                        f"**Bild-Analyse / Inhalt ({filename}):**\n\n"
                        f"> {ai_meaning}\n\n"
                        f"[Originaldatei ansehen]({source_link})"
                        if source_link
                        else f"**Bild:** {filename}"
                    )
                    txt = f"Bild-Inhalt ({filename}):\n{ai_meaning}"
                else:
                    md = (
                        f"[Bild: {filename} | Quelle: {source_link}]"
                        if source_link
                        else f"[Bild: {filename}]"
                    )
                    txt = md
                return md, txt

            try:
                # If it is HTML file (e.g. index.html from mod_page or mod_book)
                if ext in ("html", "htm") or (data[:2048] and b"<html" in data[:2048].lower()):
                    for enc in ("utf-8", "cp1252", "latin-1"):
                        try:
                            html_text = data.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        html_text = data.decode("utf-8", errors="replace")
                    md = markdownify.markdownify(html_text, heading_style="ATX").strip()
                    cleaned_md = clean_image_placeholders(md, source_url=source_link)
                    return cleaned_md, cleaned_md

                segments = extract(data, filename=filename)
                md_parts: list[str] = []
                txt_parts: list[str] = []

                for seg in segments:
                    seg_text = (seg.text or "").strip()
                    if not seg_text:
                        continue
                    seg_text_clean = clean_image_placeholders(seg_text, source_url=source_link)
                    if seg.label:
                        md_parts.append(f"## {seg.label}\n\n{seg_text_clean}")
                        txt_parts.append(f"--- {seg.label} ---\n{seg_text_clean}")
                    elif seg.page is not None:
                        md_parts.append(f"## Seite {seg.page}\n\n{seg_text_clean}")
                        txt_parts.append(f"--- Seite {seg.page} ---\n{seg_text_clean}")
                    else:
                        md_parts.append(seg_text_clean)
                        txt_parts.append(seg_text_clean)

                if md_parts:
                    return "\n\n".join(md_parts), "\n\n".join(txt_parts)
            except Exception as exc:
                log.warning("export.extract_blob_failed", doc_id=doc_id, error=str(exc))

    # 3. Chunks available in database
    chunk_rows = store.connection.execute(
        "SELECT text, page, header_text, meta FROM chunks WHERE doc_id = ? ORDER BY ordinal",
        (doc_id,),
    ).fetchall()
    if chunk_rows:
        md_parts = []
        txt_parts = []
        for chunk_text, page, _header_text, meta_json in chunk_rows:
            import json

            meta = json.loads(meta_json) if meta_json else {}
            if meta.get("summary") or meta.get("questions"):
                continue
            body_clean = (meta.get("body") or chunk_text or "").strip()
            if not body_clean:
                continue
            body_clean = clean_image_placeholders(body_clean, source_url=source_link)
            if page is not None:
                md_parts.append(f"## Seite {page}\n\n{body_clean}")
                txt_parts.append(f"--- Seite {page} ---\n{body_clean}")
            else:
                md_parts.append(body_clean)
                txt_parts.append(body_clean)

        if md_parts:
            return "\n\n".join(md_parts), "\n\n".join(txt_parts)

    # 4. Fallback for files without text extractors
    fallback_parts = []
    if ai_meaning:
        fallback_parts.append(f"**Inhalt / Beschreibung ({filename}):**\n> {ai_meaning}")
    elif external_url:
        fallback_parts.append(f"**Externer Link:** [{external_url}]({external_url})")
    elif file_url:
        filesize = doc.get("filesize", 0)
        size_str = f" ({filesize:,} Bytes)" if filesize else ""
        fallback_parts.append(f"**Datei-Anhang:** `{filename}`{size_str}")
        fallback_parts.append(f"[Datei herunterladen]({file_url})")
    else:
        fallback_parts.append("*Kein Textinhalt verfügbar.*")

    content = "\n\n".join(fallback_parts)
    return content, content


def export_all(
    store: Store,
    output_dir: Path,
    combined_txt_path: Path,
    combined_md_path: Path | None = None,
    target_chunk_tokens: int = 800_000,
    pii_tokenizer: PiiTokenizer | None = None,
) -> dict[str, Any]:
    """Export all active Moodle documents to markdown, master text, and ~800k token chunks."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if combined_txt_path.parent:
        combined_txt_path.parent.mkdir(parents=True, exist_ok=True)
    if combined_md_path and combined_md_path.parent:
        combined_md_path.parent.mkdir(parents=True, exist_ok=True)

    blobs_dir = store.blobs_dir

    cursor = store.connection.cursor()
    cursor.execute("""
        SELECT doc_id, course_id, course_name, section_name, module_id, module_name,
               modname, title, kind, header_path, module_url, timemodified,
               text, file_url, filename, filesize, mimetype, external_url,
               blob_sha256
        FROM documents
        WHERE tombstoned_at IS NULL
        ORDER BY course_name, section_name, module_name, title, doc_id
    """)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    documents = [dict(zip(columns, r, strict=True)) for r in rows]

    total_docs = len(documents)
    courses = sorted({d["course_name"] for d in documents})
    log.info("export.start", total_docs=total_docs, total_courses=len(courses))

    combined_txt_lines: list[str] = []
    combined_md_lines: list[str] = []

    now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    banner = "=" * 80
    combined_txt_lines.append(banner)
    combined_txt_lines.append("MOODLE COMPREHENSIVE TEXT EXPORT - ALL COURSES & DOCUMENTS COMBINED")
    combined_txt_lines.append(f"Generated: {now_str}")
    combined_txt_lines.append(f"Total Courses: {len(courses)}")
    combined_txt_lines.append(f"Total Documents / Activities: {total_docs}")
    combined_txt_lines.append(banner)
    combined_txt_lines.append("\nTABLE OF CONTENTS:")
    current_c = None
    for d in documents:
        if d["course_name"] != current_c:
            current_c = d["course_name"]
            c_count = sum(1 for x in documents if x["course_name"] == current_c)
            combined_txt_lines.append(f"  - {current_c} ({c_count} items)")
    combined_txt_lines.append("\n" + banner + "\n\n")

    if combined_md_path:
        combined_md_lines.append("# Moodle Comprehensive Export - All Courses & Documents\n")
        combined_md_lines.append(
            f"> **Generated:** {now_str}  \n"
            f"> **Total Courses:** {len(courses)}  \n"
            f"> **Total Documents:** {total_docs}\n\n---\n"
        )

    used_filenames: set[str] = set()
    exported_count = 0
    with_text_count = 0
    doc_blocks_txt: list[str] = []
    doc_blocks_md: list[str] = []

    for idx, doc in enumerate(documents, start=1):
        md_content, plain_content = extract_document_markdown(doc, store, blobs_dir)
        # PII tokenization (spec 013 AC-22): export is a local, user-triggered
        # action that never leaves the machine, so real values are what is wanted.
        # Every source `extract_document_markdown` reads from is raw now — inline
        # `documents.text`, blob re-extraction, and the chunks fallback alike —
        # so this only has to resolve tokens an older, pre-migration index still
        # holds in `chunks.text`.
        if pii_tokenizer is not None:
            md_content = pii_tokenizer.detokenize(md_content)
            plain_content = pii_tokenizer.detokenize(plain_content)
        if plain_content and not plain_content.startswith("*Kein Textinhalt"):
            with_text_count += 1

        title = doc.get("title") or doc.get("module_name") or "Untitled"
        course_name = doc.get("course_name") or "Unknown Course"
        course_id = doc.get("course_id", 0)
        section_name = doc.get("section_name") or "General"
        module_name = doc.get("module_name") or ""
        modname = doc.get("modname") or ""
        doc_id = doc.get("doc_id", "")
        kind = doc.get("kind", "")
        module_url = doc.get("module_url") or ""
        file_url = doc.get("file_url") or ""
        filename = doc.get("filename") or ""
        external_url = doc.get("external_url") or ""
        timemodified_str = format_timestamp(doc.get("timemodified"))

        source_info = filename or external_url or file_url or "Inline Moodle Content"

        safe_course = sanitize_filename(course_name, max_len=30)
        safe_section = sanitize_filename(section_name, max_len=25)
        safe_title = sanitize_filename(title, max_len=40)
        safe_doc_id = sanitize_filename(doc_id.replace(":", "_"), max_len=20)

        base_fname = f"[{safe_course}] [{safe_section}] {safe_title}_{safe_doc_id}.md"
        fname = base_fname
        counter = 1
        while fname in used_filenames:
            fname = f"[{safe_course}] [{safe_section}] {safe_title}_{safe_doc_id}_{counter}.md"
            counter += 1
        used_filenames.add(fname)

        doc_md = [
            f"# {title}\n",
            f"> **Course:** {course_name} (ID: {course_id})  ",
            f"> **Section:** {section_name}  ",
            f"> **Module:** {module_name} ({modname})  " if module_name else "",
            f"> **Type:** {kind}  ",
            f"> **Source:** {source_info}  ",
            f"> **Moodle URL:** [{module_url}]({module_url})  " if module_url else "",
            f"> **Last Modified:** {timemodified_str}  ",
            "\n---\n",
            md_content,
            "",
        ]
        doc_md_filtered = "\n".join(line for line in doc_md if line != "")
        (output_dir / fname).write_text(doc_md_filtered, encoding="utf-8")
        exported_count += 1

        # Text block for this document
        doc_txt_block = (
            f"{banner}\n"
            f"DOCUMENT {idx} of {total_docs}\n"
            f"COURSE:        {course_name} (ID: {course_id})\n"
            f"SECTION:       {section_name}\n"
            f"MODULE:        {module_name} ({modname})\n"
            f"TITLE:         {title}\n"
            f"KIND:          {kind}\n"
            f"SOURCE:        {source_info}\n"
            + (f"MOODLE URL:    {module_url}\n" if module_url else "")
            + f"LAST MODIFIED: {timemodified_str}\n"
            f"EXPORT FILE:   {fname}\n"
            f"{banner}\n\n"
            f"{plain_content}\n\n\n"
        )
        doc_blocks_txt.append(doc_txt_block)
        combined_txt_lines.append(doc_txt_block)

        # Markdown block for this document
        if combined_md_path:
            doc_md_block = (
                f"\n\n---\n\n## [{course_name}] {title}\n"
                f"- **Course:** {course_name} (ID: {course_id})\n"
                f"- **Section:** {section_name}\n"
                + (f"- **Module:** {module_name} ({modname})\n" if module_name else "")
                + f"- **Type:** {kind}\n"
                f"- **Source:** {source_info}\n"
                + (f"- **Moodle URL:** [{module_url}]({module_url})\n" if module_url else "")
                + f"- **Last Modified:** {timemodified_str}\n\n"
                f"{md_content}\n"
            )
            doc_blocks_md.append(doc_md_block)
            combined_md_lines.append(doc_md_block)

    # 1. Write master combined files
    combined_txt_content = "".join(combined_txt_lines)
    combined_txt_path.write_text(combined_txt_content, encoding="utf-8")
    (output_dir / "all_content_combined.txt").write_text(combined_txt_content, encoding="utf-8")

    combined_md_content = ""
    if combined_md_path:
        combined_md_content = "".join(combined_md_lines)
        combined_md_path.write_text(combined_md_content, encoding="utf-8")
        (output_dir / "all_content_combined.md").write_text(combined_md_content, encoding="utf-8")

    # 2. Automatically split into ~800,000 token chunks along document boundaries
    enc = tiktoken.get_encoding("cl100k_base")
    total_tokens = len(enc.encode(combined_txt_content))

    chunks_dir = Path("chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    export_chunks_dir = output_dir / "chunks"
    export_chunks_dir.mkdir(parents=True, exist_ok=True)

    header_txt = combined_txt_lines[0] if combined_txt_lines else ""
    header_toks = len(enc.encode(header_txt))

    clean_chunks: list[tuple[int, list[str], list[str]]] = []
    curr_txt: list[str] = []
    curr_md: list[str] = []
    curr_tokens = header_toks

    for t_blk, m_blk in zip(doc_blocks_txt, doc_blocks_md, strict=True):
        t_toks = len(enc.encode(t_blk))
        if curr_tokens + t_toks > target_chunk_tokens and curr_txt:
            clean_chunks.append((curr_tokens, curr_txt, curr_md))
            curr_txt = [t_blk]
            curr_md = [m_blk]
            curr_tokens = t_toks
        else:
            curr_txt.append(t_blk)
            curr_md.append(m_blk)
            curr_tokens += t_toks

    if curr_txt:
        clean_chunks.append((curr_tokens, curr_txt, curr_md))

    chunk_files: list[str] = []
    for c_idx, (c_toks, t_list, m_list) in enumerate(clean_chunks, start=1):
        txt_fname = f"all_content_part_{c_idx}_of_{len(clean_chunks)}.txt"
        md_fname = f"all_content_part_{c_idx}_of_{len(clean_chunks)}.md"

        chunk_header_txt = (
            f"{banner}\n"
            f"MOODLE CONTENT CHUNK {c_idx} of {len(clean_chunks)} (Target ~800,000 Tokens)\n"
            f"Tokens in this chunk: {c_toks:,} (cl100k_base)\n"
            f"Documents included: {len(t_list)}\n"
            f"{banner}\n\n"
        )
        chunk_full_txt = chunk_header_txt + "".join(t_list)
        (chunks_dir / txt_fname).write_text(chunk_full_txt, encoding="utf-8")
        (export_chunks_dir / txt_fname).write_text(chunk_full_txt, encoding="utf-8")

        chunk_header_md = (
            f"# Moodle Export - Part {c_idx} of {len(clean_chunks)}\n\n"
            f"> **Tokens:** {c_toks:,}  \n"
            f"> **Documents:** {len(m_list)}\n\n---\n\n"
        )
        chunk_full_md = chunk_header_md + "".join(m_list)
        (chunks_dir / md_fname).write_text(chunk_full_md, encoding="utf-8")
        (export_chunks_dir / md_fname).write_text(chunk_full_md, encoding="utf-8")
        chunk_files.append(str(chunks_dir / txt_fname))

    log.info(
        "export.done",
        exported_files=exported_count,
        with_text=with_text_count,
        total_tokens=total_tokens,
        num_chunks=len(clean_chunks),
    )

    return {
        "total_documents": total_docs,
        "exported_files": exported_count,
        "with_text": with_text_count,
        "total_tokens": total_tokens,
        "output_dir": str(output_dir),
        "combined_txt_path": str(combined_txt_path),
        "combined_txt_size_bytes": combined_txt_path.stat().st_size,
        "combined_md_path": str(combined_md_path) if combined_md_path else None,
        "chunks": chunk_files,
    }
