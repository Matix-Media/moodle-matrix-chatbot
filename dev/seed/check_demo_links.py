"""Fetch every external link of the demo dataset the way the API does, and report.

The demo dataset links to real TaskCards boards, HackMD notes, Google Docs/Slides/Sheets,
Nextcloud shares and YouTube videos (see ``demo/external.py``). They live on services we
don't control, so they can disappear. This runs each one through the API's own
``fetch_external`` and extractors and prints what came back:

    python dev/seed/check_demo_links.py

Exit code 1 if any link fails. Needs network access; not part of the test suite.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo.external import URLS

from moodle_llm_chat.ingest import extract as ex
from moodle_llm_chat.ingest.sources import FetchedBytes, FetchedText, fetch_external


async def check(http: httpx.AsyncClient, name: str, url: str) -> str | None:
    """The extracted text, or None when nothing usable came back."""
    result = await fetch_external(url, title=name, http=http, max_bytes=20_000_000)
    if isinstance(result, FetchedText):
        return result.text
    if isinstance(result, FetchedBytes):
        segments = ex.extract(result.data, filename=result.filename)
        return "\n".join(s.text for s in segments)
    return None


async def main() -> int:
    failures = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for name, url in URLS.items():
            try:
                text = await check(http, name, url)
            except Exception as exc:  # report every failure, keep checking the rest
                text, error = None, f"{type(exc).__name__}: {exc}"
            else:
                error = "nothing indexable"
            if text and text.strip():
                preview = " ".join(text.split())[:70]
                print(f"ok    {name:<18} {len(text):>6} chars  {preview}")
            else:
                failures += 1
                print(f"FAIL  {name:<18} {error}  {url}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
