"""Google Docs/Slides/Sheets export URLs — see ``specs/011-external-adapters.md``.

``.../export?format=X`` (or ``/export/X`` for Slides) works without auth *only* for
documents shared "anyone with the link" — the common case for material a teacher
pastes into Moodle. A restricted document fails the fetch cleanly rather than
producing garbage (handled at the indexer level, since it depends on the actual
HTTP response, not on URL shape).
"""

from __future__ import annotations

from urllib.parse import urlsplit


class GoogleDocsUnsupported(ValueError):
    """Not an exportable Google Docs/Slides/Sheets URL."""


#: (product path segment) -> (export URL builder, extractor kind for the indexer)
_PRODUCTS: dict[str, tuple[str, str]] = {
    "document": ("export?format=txt", "text"),
    "presentation": ("export/pptx", "pptx"),
    "spreadsheets": ("export?format=xlsx", "xlsx"),
}


def google_export_url(url: str) -> tuple[str, str]:
    """Return (export_url, extractor_kind) for a Google Docs/Slides/Sheets URL."""
    parts = urlsplit(url)
    if parts.netloc != "docs.google.com":
        raise GoogleDocsUnsupported(f"not a Google Docs URL: {url!r}")

    # Split without dropping empty segments: a genuinely missing id ("d//edit")
    # must be caught, not silently shift the next segment into its place.
    segments = parts.path.split("/")
    if len(segments) < 4 or segments[2] != "d":
        raise GoogleDocsUnsupported(f"unrecognised Google Docs URL shape: {url!r}")
    product, doc_id = segments[1], segments[3]
    if product not in _PRODUCTS or not doc_id:
        raise GoogleDocsUnsupported(f"unsupported Google product or missing id: {url!r}")

    suffix, kind = _PRODUCTS[product]
    return f"https://docs.google.com/{product}/d/{doc_id}/{suffix}", kind
