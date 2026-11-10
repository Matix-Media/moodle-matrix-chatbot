"""FastAPI application factory.

The API process is the only one that opens the encrypted store. Every route is
``async def``: FastAPI would dispatch sync routes to a threadpool, and the SQLite
connection belongs to the event loop thread that opened it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from moodle_llm_chat import __version__
from moodle_llm_chat.config import ConfigError, Settings
from moodle_llm_chat.crypto.keys import KeyRing, load_master_key
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.ingest.pipeline import Pipeline
from moodle_llm_chat.llm.embed import CachingEmbedder
from moodle_llm_chat.llm.factory import build_chat_model, build_embeddings
from moodle_llm_chat.llm.ocr import VisionOCR
from moodle_llm_chat.web.routes import ingest

log = structlog.get_logger(__name__)

DocumentListener = Callable[[str, list[str], bool], None]
BlobListener = Callable[[str, str], None]


def build_embedder(settings: Settings, store: Store) -> CachingEmbedder | None:
    try:
        cfg = settings.require_embedding()
    except ConfigError as exc:
        log.warning("app.embeddings_unconfigured", reason=str(exc).splitlines()[0])
        return None
    return CachingEmbedder(
        query_api=build_embeddings(cfg, task="query"),
        document_api=build_embeddings(cfg, task="document"),
        cache=store,
        provider=cfg.provider.value,
        model=cfg.model,
        dim=cfg.dim,
        batch_size=cfg.batch_size,
        rpm=cfg.rpm,
        items_per_minute=cfg.items_per_minute,
    )


def build_ocr(settings: Settings, store: Store) -> VisionOCR | None:
    if not settings.ocr.enabled:
        return None
    llm = settings.require_llm()
    if settings.ocr.model:
        llm = llm.model_copy(update={"utility_model": settings.ocr.model})
    model = build_chat_model(llm, utility=True)
    return VisionOCR(model, store, model_tag=f"{llm.provider.value}:{llm.utility_model}")


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
            async with httpx.AsyncClient() as http:
                pipeline = Pipeline(
                    store,
                    embedder=build_embedder(settings, store),
                    ocr=build_ocr(settings, store),
                    http=http,
                    settings=settings.pipeline,
                )
                app.state.pipeline = pipeline
                app.state.pipeline_status = pipeline.status
                document_listeners.append(
                    lambda site, ids, bulk: pipeline.documents_changed(site, ids, bulk=bulk)
                )
                blob_listeners.append(pipeline.blob_arrived)
                if start_workers:
                    await pipeline.start()
                try:
                    yield
                finally:
                    await pipeline.stop()

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
