"""The signed-in user's token budget (chat-token bearer auth)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from moodle_llm_chat.quota.service import QuotaService, QuotaStatus
from moodle_llm_chat.web.routes.conversations import ChatUser

router = APIRouter(prefix="/api/quota", tags=["quota"])


@router.get("")
async def quota_status(v: ChatUser, request: Request) -> QuotaStatus:
    """What the chat's budget ring shows, so a page can render it before the first turn.

    The same numbers the websocket's ``quota`` frame carries after a turn. Read-only, so the
    chat token stays valid for the websocket.
    """
    quota: QuotaService = request.app.state.quota
    return quota.status(v.site.site_id, v.scope.user_id, v.quota)
