"""HTTP API for the Nuxt chat frontend, `cron`, and `matrix` — see
specs/014-web-chat.md and specs/019-microservice-split.md.

The only process that ever opens the SQLite `Store` directly. Everything else
(`matrix`, `cron`, `web`) talks to it over HTTP — see this package's `routes/`.

Every route in this app must be `async def`, never a plain `def`: FastAPI
dispatches sync routes to a threadpool worker, which can land on a different
thread than the one `lifespan` opened the sqlite3 connection on —
"SQLite objects created in a thread can only be used in that same thread",
seen live in production before `/api/ask` was fixed. `pipeline.answer()` and
friends stay blocking calls inside these `async def` routes — acceptable here,
the same tradeoff the Matrix bot already made.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI

from bsbot.api.index.aliases import load_aliases
from bsbot.api.index.search import HybridSearcher
from bsbot.api.index.store import Store
from bsbot.api.indexer import Indexer
from bsbot.api.llm.embed import GeminiEmbedder
from bsbot.api.llm.gemini import GeminiClient
from bsbot.api.pii import build_pii_tokenizer
from bsbot.api.rag.pipeline import AnswerPipeline
from bsbot.api.rate_limit import RateLimiter
from bsbot.api.routes import ask, crawl, embed, fetch_cache, ingest_message, segments
from bsbot.cron.fetcher import FetchResult
from bsbot.shared.config import Settings

DEFAULT_ALIASES_PATH = Path("config/document_aliases.yaml")


class _UnusedFetcher:
    """`Indexer.index_segments()` (what `api` calls) never fetches anything —
    only `index_pending()`/`_index_one()` (what `cron` calls instead, locally,
    via its own Store-free `Fetcher`) do. This stub exists only to satisfy
    `Indexer.__init__`'s required `fetcher` parameter.
    """

    async def fetch(self, url: str, *, moodle_timemodified: int | None = None) -> FetchResult:
        raise NotImplementedError("api's Indexer never fetches; it only calls index_segments()")


def _moodle_host(settings: Settings) -> str | None:
    return urlsplit(settings.moodle.base_url).netloc if settings.moodle.base_url else None


def create_app(settings: Settings) -> FastAPI:
    web = settings.require_web()
    gemini = settings.require_gemini()
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with Store(settings.index_db, embed_dim=gemini.embed_dim) as store:
            client = GeminiClient(gemini)
            embedder = GeminiEmbedder(
                client,
                store=store,
                model=gemini.embed_model,
                dim=gemini.embed_dim,
                batch_size=gemini.embed_batch_size,
                rpm=gemini.embed_rpm,
                items_per_minute=gemini.embed_items_per_minute,
            )
            pii_tok = build_pii_tokenizer(settings, store)
            app.state.store = store
            app.state.embedder = embedder
            app.state.pii_tokenizer = pii_tok
            app.state.indexer = Indexer(
                store,
                _UnusedFetcher(),
                aliases=load_aliases(DEFAULT_ALIASES_PATH),
                moodle_host=_moodle_host(settings),
                pii_tokenizer=pii_tok,
            )
            app.state.pipeline = AnswerPipeline(
                HybridSearcher(store, embedder=embedder, pii_tokenizer=pii_tok),
                client,
                decompose=True,
                step_back=True,
                crag=True,
                suggest_followup=True,
                moodle_base_url=settings.moodle.base_url
                if settings.moodle.base_url
                else "https://moodle.itech-bs14.de",
                utility_model=gemini.utility_model,
                pii_tokenizer=pii_tok,
            )
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter
    app.state.api_token = web.api_token.get_secret_value()

    routers = (
        ask.router,
        crawl.router,
        fetch_cache.router,
        segments.router,
        embed.router,
        ingest_message.router,
    )
    for router in routers:
        app.include_router(router)

    return app
