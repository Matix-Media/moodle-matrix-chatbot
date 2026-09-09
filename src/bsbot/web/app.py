"""HTTP API for the Nuxt chat frontend — see specs/014-web-chat.md.

A thin wrapper around the same `AnswerPipeline` the Matrix bot and `bsbot ask`
already use. Holds no session state: every follow-up's context comes entirely
from the `history` the caller resends (AC-5).
"""

from __future__ import annotations

import hmac
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from bsbot.config import Settings
from bsbot.index.search import HybridSearcher
from bsbot.index.store import Store
from bsbot.llm.embed import GeminiEmbedder
from bsbot.llm.gemini import GeminiClient
from bsbot.pii import build_pii_tokenizer
from bsbot.rag.pipeline import Answer, AnswerPipeline


class AskRequest(BaseModel):
    question: str
    history: list[tuple[str, str]] | None = None


class _RateLimiter:
    """Burst-per-minute + daily quota, mirroring `BotPolicy`'s shape (spec
    009) — but a single bucket (AC-7): every caller presents the same shared
    token, so there is no per-visitor identity to key limits on.
    """

    def __init__(
        self,
        *,
        max_per_minute: int = 20,
        max_per_day: int = 200,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_per_minute = max_per_minute
        self._max_per_day = max_per_day
        self._now = now or (lambda: datetime.now(UTC))
        self._recent: list[float] = []
        self._day: tuple[date, int] | None = None

    def allow(self) -> bool:
        now = time.monotonic()
        self._recent = [t for t in self._recent if now - t < 60.0]
        if len(self._recent) >= self._max_per_minute:
            return False

        today = self._now().date()
        last_day, count = self._day or (today, 0)
        if last_day != today:
            count = 0
        if count >= self._max_per_day:
            self._day = (last_day, count)
            return False

        self._recent.append(now)
        self._day = (today, count + 1)
        return True


def create_app(settings: Settings) -> FastAPI:
    web = settings.require_web()
    gemini = settings.require_gemini()
    limiter = _RateLimiter()

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
    def ask(request: AskRequest) -> Answer:
        if not limiter.allow():
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        pipeline: AnswerPipeline = app.state.pipeline
        return pipeline.answer(request.question, history=request.history)

    return app
