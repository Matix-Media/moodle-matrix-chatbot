"""HTTP API for the Nuxt chat frontend — see specs/014-web-chat.md.

A thin wrapper around the same `AnswerPipeline` the Matrix bot and `bsbot ask`
already use. Holds no session state: every follow-up's context comes entirely
from the `history` the caller resends (AC-5).
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from bsbot.config import Settings
from bsbot.index.search import HybridSearcher
from bsbot.index.store import Store
from bsbot.llm.embed import GeminiEmbedder
from bsbot.llm.gemini import GeminiClient
from bsbot.pii import build_pii_tokenizer
from bsbot.rag.pipeline import Answer, AnswerPipeline
from bsbot.web.rate_limit import RateLimiter
from bsbot.web.schemas import AskRequest


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

    def _verify_token(authorization: str | None = Header(default=None)) -> None:
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization[len("Bearer ") :]
        if not hmac.compare_digest(token, web.api_token.get_secret_value()):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/ask", response_model=Answer, dependencies=[Depends(_verify_token)])
    async def ask(request: AskRequest) -> Answer:
        # `async def`, not `def` — a plain `def` route is dispatched by
        # FastAPI to a threadpool worker, which can (and, live, did) land on
        # a different thread than the one `lifespan` opened the sqlite3
        # connection on: "SQLite objects created in a thread can only be
        # used in that same thread." `async def` keeps this on the single
        # event-loop thread instead, matching where `Store` was opened.
        # `pipeline.answer()` itself stays a blocking call — acceptable
        # here, same tradeoff the Matrix bot already makes.
        if not limiter.allow():
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        pipeline: AnswerPipeline = app.state.pipeline
        return pipeline.answer(request.question, history=request.history)

    return app
