"""Nextcloud public share links — see ``specs/005-fetch-cache.md``.

Teachers paste share URLs into Moodle pages and labels. Public shares need no
credentials: appending ``/download`` to the share URL returns the file. The live
site has 11 such shares on ``cloud.itech-bs14.de``.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: Matches both ``/s/<token>`` and ``/index.php/s/<token>``.
_SHARE_RE = re.compile(r"^(?P<prefix>(?:/index\.php)?)/s/(?P<token>[A-Za-z0-9_\-]{4,})/?$")


class ShareUnsupported(ValueError):
    """The URL is not a Nextcloud public share we can download."""


def is_nextcloud_share(url: str) -> bool:
    try:
        share_download_url(url)
    except ShareUnsupported:
        return False
    return True


def share_download_url(url: str) -> str:
    """Turn a public share URL into its direct-download form.

    Moodle often carries viewer state in the query string
    (``?dir=/&editing=false&openfile=true``); it must be dropped, or Nextcloud
    returns the HTML viewer instead of the file.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ShareUnsupported(f"not an http(s) URL: {url!r}")
    match = _SHARE_RE.match(parts.path)
    if not match:
        raise ShareUnsupported(f"not a Nextcloud public share: {url!r}")
    prefix, token = match.group("prefix"), match.group("token")
    return f"{parts.scheme}://{parts.netloc}{prefix}/s/{token}/download"
