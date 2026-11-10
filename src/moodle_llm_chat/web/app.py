"""FastAPI application factory.

The API process is the only one that opens the encrypted store. Every route is
``async def``: FastAPI would dispatch sync routes to a threadpool, and the SQLite
connection belongs to the event loop thread that opened it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from moodle_llm_chat import __version__
from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.keys import KeyRing, load_master_key
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.web.routes import ingest

DocumentListener = Callable[[str, list[str]], None]
BlobListener = Callable[[str, str], None]


def create_app(
    settings: Settings,
    *,
    ring: KeyRing | None = None,
    start_workers: bool = True,
) -> FastAPI:
    """Build the app. ``ring`` overrides loading the master key (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        key_ring = ring or KeyRing(load_master_key(settings.security))
        with Store(
            settings.index_db,
            key_ring,
            embed_dim=settings.embedding.dim,
            blobs_dir=settings.blobs_dir,
        ) as store:
            app.state.store = store
            app.state.ring = key_ring
            yield

    app = FastAPI(title="moodle-llm-chat", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.start_workers = start_workers
    document_listeners: list[DocumentListener] = []
    blob_listeners: list[BlobListener] = []
    app.state.document_listeners = document_listeners
    app.state.blob_listeners = blob_listeners

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(ingest.router)
    return app
