"""The recurring sync -> index -> embed cycle — the "sync cron loop" deferred
earlier in favour of retrieval-quality work (see specs/005..007).

Runs unattended, in a container, with nobody watching a terminal — so results
go through structlog (matching the rest of the app), and a failure in one step
must never take down the others or the loop itself: a Moodle outage during sync
must not also cancel an otherwise-healthy embed pass over whatever was already
indexed, the same per-item isolation philosophy as ``Indexer.index_pending``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit

import structlog

from bsbot.config import GeminiConfig, MoodleConfig, Settings
from bsbot.index.aliases import load_aliases
from bsbot.index.store import Store
from bsbot.ingest.crawler import CourseCrawler
from bsbot.ingest.fetcher import Fetcher
from bsbot.ingest.indexer import Indexer
from bsbot.llm.embed import GeminiEmbedder
from bsbot.llm.gemini import GeminiClient
from bsbot.moodle.client import MoodleClient
from bsbot.pii import build_pii_tokenizer

log = structlog.get_logger(__name__)


async def sync_once(settings: Settings, moodle: MoodleConfig, *, follow_links: bool = True) -> None:
    """Crawl every enrolled course and persist the manifest (mirrors ``bsbot sync``).

    Structure only, no files downloaded — cheap enough to run every cycle.
    """
    async with MoodleClient(moodle) as client:
        result = await CourseCrawler(
            client,
            moodle_host=urlsplit(moodle.base_url).netloc,
            follow_linked_courses=follow_links,
        ).crawl()
    with Store(settings.index_db, embed_dim=settings.gemini.embed_dim) as store:
        store.persist_crawl(result.items)
    log.info(
        "cron.sync",
        items=len(result.items),
        courses_ok=result.courses_ok,
        courses_failed=result.courses_failed,
    )


async def index_once(settings: Settings, moodle: MoodleConfig, aliases_path: Path) -> None:
    """Fetch, extract and chunk everything pending (mirrors ``bsbot index``).

    Only documents the manifest reports as pending are touched — the unchanged-
    content skip in ``Indexer._index_one`` means a cycle that finds nothing new
    costs no embedding calls at all, which is what makes running this often safe.
    """
    aliases = load_aliases(aliases_path)
    async with MoodleClient(moodle) as client:
        token = await client.login()
    with Store(settings.index_db, embed_dim=settings.gemini.embed_dim) as store:
        async with Fetcher(
            store,
            moodle_token=token,
            moodle_host=urlsplit(moodle.base_url).netloc,
            max_concurrency=moodle.max_concurrency,
        ) as fetcher:
            stats = await Indexer(
                store,
                fetcher,
                aliases=aliases,
                moodle_host=urlsplit(moodle.base_url).netloc,
                pii_tokenizer=build_pii_tokenizer(settings, store),
            ).index_pending()
    log.info(
        "cron.index",
        indexed=stats.indexed,
        chunks=stats.chunks,
        skipped=stats.skipped,
        failed=stats.failed,
    )


async def embed_once(settings: Settings, gemini: GeminiConfig) -> None:
    """Embed every chunk that does not yet have a vector (mirrors ``bsbot embed``)."""
    with Store(settings.index_db, embed_dim=gemini.embed_dim) as store:
        rows = store.connection.execute(
            "SELECT c.chunk_id, c.text FROM chunks c "
            "LEFT JOIN chunks_vec v ON v.chunk_id = c.chunk_id "
            "WHERE v.chunk_id IS NULL ORDER BY c.chunk_id"
        ).fetchall()
        if not rows:
            log.info("cron.embed", embedded=0, total=0)
            return

        embedder = GeminiEmbedder(
            GeminiClient(gemini),
            store=store,
            model=gemini.embed_model,
            dim=gemini.embed_dim,
            batch_size=gemini.embed_batch_size,
            rpm=gemini.embed_rpm,
            items_per_minute=gemini.embed_items_per_minute,
        )
        vectors = embedder.embed_documents([r["text"] for r in rows], skip_failures=True)
        done = 0
        for row, vector in zip(rows, vectors, strict=True):
            if vector is not None:
                store.set_embedding(row["chunk_id"], vector)
                done += 1
    log.info("cron.embed", embedded=done, total=len(rows))


async def _run_steps(steps: Sequence[tuple[str, Callable[[], Awaitable[None]]]]) -> None:
    """Run each step in order; one failing must not stop the rest."""
    for name, step in steps:
        try:
            await step()
        except Exception as exc:
            log.warning("cron.step_failed", step=name, error=f"{type(exc).__name__}: {exc}")


async def run_cycle(
    settings: Settings,
    moodle: MoodleConfig,
    gemini: GeminiConfig,
    aliases_path: Path,
    *,
    follow_links: bool = True,
) -> None:
    """One sync -> index -> embed pass."""
    await _run_steps(
        [
            ("sync", lambda: sync_once(settings, moodle, follow_links=follow_links)),
            ("index", lambda: index_once(settings, moodle, aliases_path)),
            ("embed", lambda: embed_once(settings, gemini)),
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
