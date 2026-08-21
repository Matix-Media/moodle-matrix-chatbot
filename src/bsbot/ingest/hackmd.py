"""HackMD notes — see ``specs/011-external-adapters.md``.

Appending ``.md`` to any note URL returns raw Markdown, confirmed against HackMD's
own documentation — no auth needed for a public note, the same shape as our
existing Nextcloud-share handling.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class HackmdUnsupported(ValueError):
    """Not a HackMD note URL."""


def is_hackmd_note(url: str) -> bool:
    try:
        hackmd_markdown_url(url)
    except HackmdUnsupported:
        return False
    return True


def hackmd_markdown_url(url: str) -> str:
    """Turn a note URL into its raw-Markdown form."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != "hackmd.io":
        raise HackmdUnsupported(f"not a HackMD note: {url!r}")
    path = parts.path.rstrip("/")
    if not path or path == "":
        raise HackmdUnsupported(f"no note id in URL: {url!r}")
    if not path.endswith(".md"):
        path += ".md"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
