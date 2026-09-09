"""The public chat endpoint — see specs/014-web-chat.md."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from bsbot.api.auth import verify_token
from bsbot.api.rag.pipeline import Answer, AnswerPipeline
from bsbot.api.schemas import AskRequest

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/ask", response_model=Answer, dependencies=[Depends(verify_token)])
async def ask(request: Request, body: AskRequest) -> Answer:
    # `async def`, not `def` — see app.py's module docstring: a plain `def`
    # route is dispatched to a threadpool worker, which can land on a
    # different thread than the one `lifespan` opened the sqlite3 connection
    # on. Every route in this app must stay `async def` for the same reason.
    if not request.app.state.limiter.allow():
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    pipeline: AnswerPipeline = request.app.state.pipeline
    return pipeline.answer(
        body.question, history=body.history, room_id=body.room_id, event_id=body.event_id
    )
