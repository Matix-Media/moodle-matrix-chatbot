"""The recurring sync -> index -> embed cycle — the "sync cron loop" deferred
earlier in favour of retrieval-quality work (see specs/005..007), later split
across processes (specs/019-microservice-split.md): `cron` only crawls,
fetches, and extracts now — `api` is the only process that touches the SQLite
Store, doing all PII-tokenization, chunking, and embedding server-side.

Runs unattended, in a container, with nobody watching a terminal — so results
go through structlog (matching the rest of the app), and a failure in one step
must never take down the others or the loop itself: a Moodle outage during sync
must not also cancel an otherwise-healthy embed pass over whatever was already
indexed, the same per-item isolation philosophy as ``Indexer.index_pending``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlsplit

import structlog

from bsbot.config import MoodleConfig
from bsbot.ingest.api_client import CronApiClient
from bsbot.ingest.crawler import CourseCrawler
from bsbot.ingest.fetcher import Fetcher
from bsbot.ingest.indexer import IndexStats
from bsbot.ingest.segments import resolve_segments
from bsbot.moodle.client import MoodleClient

log = structlog.get_logger(__name__)


async def sync_once(moodle: MoodleConfig, api: CronApiClient, *, follow_links: bool = True) -> None:
    """Crawl every enrolled course and persist the manifest via `api` (mirrors
    the old ``bsbot sync``). Structure only, no files downloaded — cheap
    enough to run every cycle."""
    async with MoodleClient(moodle) as client:
        result = await CourseCrawler(
            client,
            moodle_host=urlsplit(moodle.base_url).netloc,
            follow_linked_courses=follow_links,
        ).crawl()
    persisted, _pending = api.crawl_result(result.items)
    log.info(
        "cron.sync",
        items=persisted,
        courses_ok=result.courses_ok,
        courses_failed=result.courses_failed,
    )


async def index_once(moodle: MoodleConfig, api: CronApiClient) -> None:
    """Fetch and extract everything `api` reports pending, handing resolved
    segments to it for chunking/PII/embedding/storage (mirrors the old
    ``bsbot index``, now split across two processes).

    Every pending document is submitted even when fetch/extract failed or was
    skipped locally (segments=None then) — `api`'s own alias-chunk handling
    (`Indexer.index_segments`) still needs the chance to run, exactly like the
    single-process version always proceeded past a failed fetch to check for
    an alias.
    """
    async with MoodleClient(moodle) as client:
        token = await client.login()
    moodle_host = urlsplit(moodle.base_url).netloc

    stats = IndexStats()
    async with Fetcher(
        api,
        moodle_token=token,
        moodle_host=moodle_host,
        max_concurrency=moodle.max_concurrency,
    ) as fetcher:
        for document in api.pending_documents():
            try:
                outcome = await resolve_segments(document, fetcher, moodle_host=moodle_host)
                # Counted locally too, not just from api's response: a failed/
                # skipped fetch still gets submitted below (segments=None) so
                # api's alias-chunk handling gets a chance to run, but with no
                # alias present that call touches none of api's own counters
                # — leaving the failure invisible in this summary otherwise.
                if outcome.skipped:
                    stats.skipped += 1
                elif outcome.failed:
                    stats.failed += 1
                result = api.index_segments(document.doc_id, outcome.segments, outcome.blob_sha256)
                stats.indexed += result.get("indexed", 0)
                stats.chunks += result.get("chunks", 0)
                stats.skipped += result.get("skipped", 0)
                stats.failed += result.get("failed", 0)
            except Exception as exc:  # never let one document abort the batch
                stats.failed += 1
                log.warning(
                    "cron.index_one_failed",
                    doc_id=document.doc_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
    log.info(
        "cron.index",
        indexed=stats.indexed,
        chunks=stats.chunks,
        skipped=stats.skipped,
        failed=stats.failed,
    )


async def embed_once(api: CronApiClient) -> None:
    """Trigger `api` to embed every chunk that doesn't yet have a vector
    (mirrors the old ``bsbot embed`` — the actual embedding now runs
    server-side, see `bsbot.web.routes.embed`)."""
    result = api.embed_pending()
    log.info("cron.embed", embedded=result.get("embedded", 0), total=result.get("total", 0))


async def _run_steps(steps: Sequence[tuple[str, Callable[[], Awaitable[None]]]]) -> None:
    """Run each step in order; one failing must not stop the rest."""
    for name, step in steps:
        try:
            await step()
        except Exception as exc:
            log.warning("cron.step_failed", step=name, error=f"{type(exc).__name__}: {exc}")


async def run_cycle(moodle: MoodleConfig, api: CronApiClient, *, follow_links: bool = True) -> None:
    """One sync -> index -> embed pass."""
    await _run_steps(
        [
            ("sync", lambda: sync_once(moodle, api, follow_links=follow_links)),
            ("index", lambda: index_once(moodle, api)),
            ("embed", lambda: embed_once(api)),
        ]
    )


async def run_forever(
    cycle: Callable[[], Awaitable[None]],
    *,
    interval_minutes: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """Repeat ``cycle`` every ``interval_minutes``, forever unless ``max_cycles``
    is given (tests only — production always runs unbounded)."""
    interval_s = interval_minutes * 60
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        log.info("cron.cycle_started")
        await cycle()
        cycles += 1
        log.info("cron.cycle_finished", next_in_minutes=interval_minutes)
        if max_cycles is None or cycles < max_cycles:
            await sleep(interval_s)


__all__ = [
    "embed_once",
    "index_once",
    "run_cycle",
    "run_forever",
    "sync_once",
]
