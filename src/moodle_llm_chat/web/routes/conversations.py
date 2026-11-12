"""Conversation history for the chat UI (chat-token bearer auth, owner only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from moodle_llm_chat.access.tokens import TokenError, VerifiedToken, verify_chat_token
from moodle_llm_chat.index.store import StoreError
from moodle_llm_chat.web.auth import UNAUTHORIZED, StoreDep, bearer_token, require_tls

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


async def chat_user(
    request: Request,
    store: StoreDep,
    _tls: Annotated[None, Depends(require_tls)],
) -> VerifiedToken:
    token = bearer_token(request)
    if token is None:
        raise UNAUTHORIZED
    try:
        # Read-only listing does not burn the token; websocket frames do.
        return verify_chat_token(token, store, consume=False)
    except TokenError as exc:
        raise UNAUTHORIZED from exc


ChatUser = Annotated[VerifiedToken, Depends(chat_user)]

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.get("")
async def list_conversations(v: ChatUser, store: StoreDep) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "title": c.title,
            "course_id": c.course_id,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
        }
        for c in store.conversations(v.site.site_id, v.scope.user_id)
    ]


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    v: ChatUser,
    store: StoreDep,
    request: Request,
    after_seq: int = 0,
) -> dict[str, Any]:
    """Stored history after ``after_seq``: one event per streamed run, not per delta.

    A run still being streamed is not stored yet; the websocket resumes it from
    ``last_seq``. ``tools`` is the same UI metadata as the websocket's ``ready`` frame,
    so a server-side renderer can label tool cards.
    """
    try:
        conversation = store.conversation(v.site.site_id, v.scope.user_id, conversation_id)
        rows = store.events(v.site.site_id, v.scope.user_id, conversation_id, after_seq=after_seq)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return {
        "conversation": conversation.model_dump(exclude={"site_id", "user_id"}),
        "events": [e for _, e in rows],
        "last_seq": rows[-1][0] if rows else after_seq,
        "tools": request.app.state.registry.manifest(),
    }


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: Rename, v: ChatUser, store: StoreDep
) -> dict[str, str]:
    try:
        store.set_conversation_title(v.site.site_id, v.scope.user_id, conversation_id, body.title)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return {"id": conversation_id, "title": body.title}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_conversation(conversation_id: str, v: ChatUser, store: StoreDep) -> Response:
    try:
        store.archive_conversation(v.site.site_id, v.scope.user_id, conversation_id)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
