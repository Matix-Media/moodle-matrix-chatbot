"""``/ws/chat`` — the browser chat protocol (JSON frames, protocol version 1).

Client → server::

    auth     {token}                                  first frame, within 10 s
    send     {text, token, conversation_id?, course_id?, client_msg_id?}
    answer   {conversation_id, interrupt_id, response, token}
    resume   {conversation_id, last_seq}             after a reconnect
    continue {conversation_id, turn_id, token}       after turn_interrupted
    cancel   {conversation_id}
    ping

Server → client: ``ready``, ``accepted``, ``pong``, ``error``, ``rate_limited``, and
every persisted turn event (``user_message``, ``thinking``, ``token``, ``tool_call``,
``tool_result``, ``question``, ``final``, ``quota``, …) carrying ``seq``. Clients must
ignore unknown frame types and fields.

State-changing frames carry a fresh chat token (single-use ``jti``), so the scope —
which course modules the user may see — is at most one message old. Close codes:
4400 TLS required, 4401 unauthorized, 4403 origin not allowed, 4429 too many
connections, 4503 chat unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from moodle_llm_chat.access.tokens import TokenError, VerifiedToken, verify_chat_token
from moodle_llm_chat.agent.events import PROTOCOL_VERSION
from moodle_llm_chat.chat.service import ChatError, ChatService
from moodle_llm_chat.index.store import Store

router = APIRouter()
log = structlog.get_logger(__name__)

AUTH_TIMEOUT_S = 10.0


class _Connection:
    def __init__(self, ws: WebSocket, chat: ChatService, store: Store, auth: VerifiedToken) -> None:
        self.ws = ws
        self.chat = chat
        self.store = store
        self.auth = auth
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.forwarders: dict[str, tuple[asyncio.Queue[dict[str, Any]], asyncio.Task[None]]] = {}
        self.last_seq: dict[str, int] = {}

    def emit(self, frame: dict[str, Any]) -> None:
        self.outbox.put_nowait({"v": PROTOCOL_VERSION, **frame})

    async def sender(self) -> None:
        while True:
            frame = await self.outbox.get()
            await self.ws.send_json(frame)

    def attach(self, conversation_id: str, after_seq: int) -> None:
        """Replay events after ``after_seq``, then forward live ones (deduplicated by seq)."""
        if conversation_id in self.forwarders:
            return
        queue = self.chat.subscribe(conversation_id)
        try:
            backlog = self.chat.replay(self.auth, conversation_id, after_seq=after_seq)
        except ChatError:
            self.chat.unsubscribe(conversation_id, queue)
            raise
        self.last_seq[conversation_id] = after_seq
        for event in backlog:
            self._forward(conversation_id, event)
        task = asyncio.create_task(self._pump(conversation_id, queue))
        self.forwarders[conversation_id] = (queue, task)

    def _forward(self, conversation_id: str, event: dict[str, Any]) -> None:
        seq = int(event.get("seq", 0))
        if seq <= self.last_seq.get(conversation_id, 0):
            return
        self.last_seq[conversation_id] = seq
        self.emit({**event, "conversation_id": conversation_id})

    async def _pump(self, conversation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            self._forward(conversation_id, await queue.get())

    def close(self) -> None:
        for conversation_id, (queue, task) in self.forwarders.items():
            task.cancel()
            self.chat.unsubscribe(conversation_id, queue)
        self.forwarders.clear()

    def fresh_token(self, frame: dict[str, Any]) -> VerifiedToken:
        token = frame.get("token")
        if not isinstance(token, str):
            raise ChatError("unauthorized", "Ein aktuelles Chat-Token fehlt.")
        try:
            verified = verify_chat_token(token, self.store, consume=True)
        except TokenError as exc:
            raise ChatError("unauthorized", "Das Chat-Token ist ungültig.") from exc
        if (
            verified.site.site_id != self.auth.site.site_id
            or verified.scope.user_id != self.auth.scope.user_id
        ):
            raise ChatError("auth_mismatch", "Das Token gehört zu einem anderen Nutzer.")
        self.auth = verified
        return verified

    async def handle(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "ping":
            self.emit({"type": "pong", "server_time": int(time.time())})
        elif kind == "send":
            v = self.fresh_token(frame)
            limiter = self.ws.app.state.burst
            if not limiter.allow((v.site.site_id, v.scope.user_id)):
                raise ChatError("rate_limited", "Zu viele Nachrichten.", scope="burst")
            conversation_id, turn_id = await self.chat.send(
                v,
                conversation_id=frame.get("conversation_id"),
                course_id=frame.get("course_id"),
                text=str(frame.get("text", "")),
                client_msg_id=frame.get("client_msg_id"),
            )
            self.emit(
                {
                    "type": "accepted",
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "client_msg_id": frame.get("client_msg_id"),
                }
            )
            self.attach(conversation_id, self.last_seq.get(conversation_id, 0))
        elif kind == "answer":
            v = self.fresh_token(frame)
            conversation_id = str(frame.get("conversation_id", ""))
            self.attach(conversation_id, self.last_seq.get(conversation_id, 0))
            await self.chat.answer(
                v,
                conversation_id=conversation_id,
                interrupt_id=str(frame.get("interrupt_id", "")),
                response=dict(frame.get("response") or {}),
            )
        elif kind == "resume":
            conversation_id = str(frame.get("conversation_id", ""))
            self.attach(conversation_id, int(frame.get("last_seq", 0)))
        elif kind == "continue":
            v = self.fresh_token(frame)
            conversation_id = str(frame.get("conversation_id", ""))
            self.attach(conversation_id, self.last_seq.get(conversation_id, 0))
            await self.chat.continue_turn(
                v, conversation_id=conversation_id, turn_id=str(frame.get("turn_id", ""))
            )
        elif kind == "cancel":
            await self.chat.cancel(self.auth, conversation_id=str(frame.get("conversation_id", "")))
        else:
            raise ChatError("unknown_type", f"Unbekannter Nachrichtentyp: {kind!r}")


def _origin_allowed(ws: WebSocket, site_url: str) -> bool:
    origin = ws.headers.get("origin")
    # Browsers always send Origin; a missing one is a non-browser client.
    return origin is None or origin.rstrip("/") == site_url.rstrip("/")


@router.websocket("/ws/chat")
async def chat_socket(ws: WebSocket) -> None:
    app = ws.app
    await ws.accept()
    if app.state.settings.security.require_tls and ws.url.scheme != "wss":
        await ws.close(code=4400, reason="TLS required")
        return
    chat: ChatService | None = getattr(app.state, "chat", None)
    if chat is None:
        await ws.close(code=4503, reason="chat unavailable")
        return
    store: Store = app.state.store
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_S)
        if not isinstance(first, dict) or first.get("type") != "auth":
            raise TokenError("first frame must be auth")
        auth = verify_chat_token(str(first.get("token", "")), store, consume=True)
    except (TokenError, TimeoutError, ValueError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await ws.close(code=4401, reason="unauthorized")
        return
    if not _origin_allowed(ws, auth.site.url):
        await ws.close(code=4403, reason="origin not allowed")
        return

    key = (auth.site.site_id, auth.scope.user_id)
    counts: dict[tuple[str, int], int] = app.state.ws_counts
    if counts.get(key, 0) >= app.state.settings.server.max_ws_per_user:
        await ws.close(code=4429, reason="too many connections")
        return
    counts[key] = counts.get(key, 0) + 1

    conn = _Connection(ws, chat, store, auth)
    sender = asyncio.create_task(conn.sender())
    conn.emit(
        {
            "type": "ready",
            "tools": app.state.registry.manifest(),
            "server_time": int(time.time()),
            "user": {"name": auth.user.fullname},
        }
    )
    try:
        while True:
            frame = await ws.receive_json()
            if not isinstance(frame, dict):
                conn.emit({"type": "error", "code": "bad_frame", "message": "Ungültige Nachricht."})
                continue
            try:
                await conn.handle(frame)
            except ChatError as exc:
                if exc.code == "rate_limited":
                    conn.emit({"type": "rate_limited", **exc.details})
                else:
                    conn.emit(
                        {
                            "type": "error",
                            "code": exc.code,
                            "message": exc.message,
                            "ref": frame.get("type"),
                        }
                    )
    except WebSocketDisconnect:
        pass
    except ValueError:  # malformed JSON
        with contextlib.suppress(Exception):
            await ws.close(code=1003)
    finally:
        conn.close()
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        counts[key] = max(0, counts.get(key, 1) - 1)
