"""FastAPI application factory.

The API process is the only one that opens the encrypted store. Every route and
dependency is ``async def``: FastAPI would dispatch sync ones to a threadpool, and
the SQLite connection belongs to the event loop thread that opened it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from fastapi import FastAPI
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from moodle_llm_chat import __version__
from moodle_llm_chat.access.moodle import MoodleAccessClient
from moodle_llm_chat.agent.checkpoint import EncryptedCheckpointer
from moodle_llm_chat.agent.context import AccessChecker
from moodle_llm_chat.agent.runner import AgentRunner
from moodle_llm_chat.agent.tools import default_registry
from moodle_llm_chat.chat.service import ChatService
from moodle_llm_chat.config import ConfigError, Settings
from moodle_llm_chat.crypto.keys import KeyRing, load_master_key
from moodle_llm_chat.index.search import Searcher
from moodle_llm_chat.index.store import Store
from moodle_llm_chat.ingest.pipeline import Pipeline
from moodle_llm_chat.llm.embed import CachingEmbedder
from moodle_llm_chat.llm.factory import build_chat_model, build_embeddings
from moodle_llm_chat.llm.ocr import VisionOCR
from moodle_llm_chat.quota.service import QuotaService
from moodle_llm_chat.web.limits import BurstLimiter, SiteCORS
from moodle_llm_chat.web.routes import chat_ws, conversations, ingest, site_admin

log = structlog.get_logger(__name__)

DocumentListener = Callable[[str, list[str], bool], None]
#: Documents whose content is unchanged but whose header path moved (re-chunk only).
RelocationListener = Callable[[str, list[str]], None]
BlobListener = Callable[[str, str], None]


def build_embedder(
    settings: Settings, store: Store, override: Embeddings | None = None
) -> CachingEmbedder | None:
    if override is not None:
        return CachingEmbedder(
            query_api=override,
            document_api=override,
            cache=store,
            provider="override",
            model="override",
            dim=settings.embedding.dim,
            rpm=0,
            items_per_minute=0,
        )
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


def _chat_model(settings: Settings) -> BaseChatModel | None:
    try:
        return build_chat_model(settings.require_llm())
    except ConfigError as exc:
        log.warning("app.chat_unconfigured", reason=str(exc).splitlines()[0])
        return None


def _token_estimator() -> Callable[[str], int]:
    """Fallback for providers that report no usage: ~4 characters per token."""
    return lambda text: max(1, len(text) // 4)


def create_app(
    settings: Settings,
    *,
    ring: KeyRing | None = None,
    start_workers: bool = True,
    chat_model: BaseChatModel | None = None,
    embeddings: Embeddings | None = None,
    access: AccessChecker | None = None,
) -> FastAPI:
    """Build the app.

    ``ring``, ``chat_model`` and ``access`` override the configured master key, chat
    model and Moodle access client (tests and tooling).
    """
    registry = default_registry()
    tz = ZoneInfo(settings.agent.timezone)

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
                embedder = build_embedder(settings, store, embeddings)
                quota = QuotaService(store, settings.quota, timezone=settings.agent.timezone)
                app.state.quota = quota
                pipeline = Pipeline(
                    store,
                    embedder=embedder,
                    ocr=build_ocr(settings, store),
                    http=http,
                    settings=settings.pipeline,
                    ingest_gate=quota.ingest_allowed,
                    ingest_meter=quota.record_ingest,
                )
                app.state.pipeline = pipeline
                app.state.pipeline_status = pipeline.status
                document_listeners.append(
                    lambda site, ids, bulk: pipeline.documents_changed(site, ids, bulk=bulk)
                )
                relocation_listeners.append(pipeline.documents_relocated)
                blob_listeners.append(pipeline.blob_arrived)

                checker: AccessChecker = access or MoodleAccessClient(
                    http,
                    secrets=store.site_secrets,
                    cache_ttl_s=settings.moodle.access_cache_ttl_s,
                    timeout_s=settings.moodle.callback_timeout_s,
                    breaker_failures=settings.moodle.circuit_breaker_failures,
                    breaker_reset_s=settings.moodle.circuit_breaker_reset_s,
                )
                model = chat_model or _chat_model(settings)
                chat: ChatService | None = None
                if model is not None:
                    runner = AgentRunner(
                        model=model,
                        registry=registry,
                        checkpointer=EncryptedCheckpointer(store),
                        max_steps=settings.agent.max_steps,
                        show_thinking=settings.agent.show_thinking,
                        estimate_tokens=_token_estimator()
                        if settings.quota.estimate_when_missing
                        else None,
                    )
                    chat = ChatService(
                        store=store,
                        runner=runner,
                        searcher=Searcher(store, embedder),
                        access=checker,
                        quota=quota,
                        agent_settings=settings.agent,
                        clock=lambda: datetime.now(tz),
                    )
                    await chat.recover()
                app.state.chat = chat
                if start_workers:
                    await pipeline.start()
                try:
                    yield
                finally:
                    if chat is not None:
                        await chat.shutdown()
                    await pipeline.stop()

    app = FastAPI(title="moodle-llm-chat", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.start_workers = start_workers
    app.state.registry = registry
    app.state.burst = BurstLimiter(settings.server.burst_per_minute)
    ws_counts: dict[tuple[str, int], int] = {}
    app.state.ws_counts = ws_counts
    document_listeners: list[DocumentListener] = []
    relocation_listeners: list[RelocationListener] = []
    blob_listeners: list[BlobListener] = []
    app.state.document_listeners = document_listeners
    app.state.relocation_listeners = relocation_listeners
    app.state.blob_listeners = blob_listeners

    def allowed_origins() -> set[str]:
        store: Store | None = getattr(app.state, "store", None)
        if store is None:
            return set()
        return {s.url.rstrip("/") for s in store.sites() if s.revoked_at is None}

    app.add_middleware(SiteCORS, allowed_origins=allowed_origins)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "chat": getattr(app.state, "chat", None) is not None,
        }

    for router in (ingest.router, site_admin.router, conversations.router, chat_ws.router):
        app.include_router(router)
    return app
