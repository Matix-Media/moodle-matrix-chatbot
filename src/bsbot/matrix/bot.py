"""Matrix bot policy and message handling — ``specs/009-matrix-bot.md``.

The nio client is injected, so everything here — who gets answered, in what form,
how often — is testable without a homeserver. That matters because the bugs that
make a bot unbearable in a shared class room are policy bugs, not protocol bugs:
answering itself, replaying the backlog after a restart, or replying to every
message from someone who is venting.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from bsbot.rag.pipeline import Answer

log = structlog.get_logger(__name__)

DEFAULT_TRIGGER = "!bs"
#: Cheap heuristic for answer_all mode. German question words plus a question mark.
_QUESTION_RE = re.compile(
    r"(\?|^\s*(wann|was|wie|wo|wer|warum|weshalb|welche[rsn]?|gibt es|kann man|muss ich)\b)",
    re.I,
)


class ClientLike(Protocol):
    async def room_send(
        self, room_id: str, message_type: str, content: dict[str, Any], **kwargs: Any
    ) -> Any: ...
    async def room_typing(self, room_id: str, typing_state: bool, timeout: int = ...) -> Any: ...


class PipelineLike(Protocol):
    def answer(self, question: str) -> Answer: ...


@dataclass
class BotPolicy:
    room_ids: set[str]
    user_id: str
    display_name: str = "bsbot"
    trigger: str = DEFAULT_TRIGGER
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    answer_all: bool = False
    max_per_user_per_minute: int = 4


class BerufsschuleBot:
    def __init__(self, client: ClientLike, pipeline: PipelineLike, policy: BotPolicy) -> None:
        self._client = client
        self._pipeline = pipeline
        self._policy = policy
        self._own_events: set[str] = set()
        self._recent: dict[str, list[float]] = {}
        self._notified: set[str] = set()

    def remember_own_message(self, event_id: str) -> None:
        """Track our own messages so a reply to one counts as addressing us."""
        self._own_events.add(event_id)

    async def handle_message(self, room: Any, event: Any, *, msgtype: str = "m.text") -> None:
        if msgtype != "m.text":  # AC-14
            return
        if room.room_id not in self._policy.room_ids:  # AC-1
            return
        sender = getattr(event, "sender", "")
        if sender == self._policy.user_id:  # AC-2
            return
        # AC-3: a restart must not re-answer the room's backlog.
        if getattr(event, "server_timestamp", 0) < self._policy.started_at_ms:
            return

        body = (getattr(event, "body", "") or "").strip()
        question = self._extract_question(body, event)
        if not question:
            return

        log.info("bot.triggered", room=room.room_id, sender=sender)

        if not self._allow(sender):  # AC-13
            if sender not in self._notified:
                self._notified.add(sender)
                await self._send(
                    room.room_id,
                    "Das sind gerade zu viele Fragen auf einmal – ich melde mich gleich wieder. 🙂",
                    reply_to=getattr(event, "event_id", None),
                )
            return
        self._notified.discard(sender)

        await self._client.room_typing(room.room_id, True)
        grounded = False
        try:
            answer = self._pipeline.answer(question)
            grounded = answer.grounded
            body_text, formatted = _render(answer)
        except Exception as exc:  # AC-10
            log.warning("bot.answer_failed", error=f"{type(exc).__name__}: {exc}")
            body_text, formatted = (
                "Da ist bei mir gerade ein Fehler aufgetreten. Bitte versuch es gleich nochmal.",
                None,
            )
        finally:
            # AC-9: a stuck "bsbot is typing…" looks broken forever.
            await self._client.room_typing(room.room_id, False)

        await self._send(
            room.room_id, body_text, formatted, reply_to=getattr(event, "event_id", None)
        )
        log.info("bot.replied", room=room.room_id, grounded=grounded)

    # ------------------------------------------------------------------ #

    def _extract_question(self, body: str, event: Any) -> str | None:
        """Decide whether this message is aimed at us, and strip the trigger (AC-4/AC-6)."""
        if not body:
            return None

        trigger = self._policy.trigger
        if body.lower().startswith(trigger.lower()):
            return body[len(trigger) :].strip(" :,\t") or None

        for name in (self._policy.user_id, self._policy.display_name):
            if not name:
                continue
            pattern = re.compile(rf"^{re.escape(name)}\s*[:,]?\s*", re.I)
            match = pattern.match(body)
            if match:
                return body[match.end() :].strip() or None

        if self._is_reply_to_us(event):
            return body

        if self._is_mentioned(event):  # AC-4: structured mention (MSC3952)
            return body

        if self._policy.answer_all and _QUESTION_RE.search(body):  # AC-5
            return body
        return None

    def _is_reply_to_us(self, event: Any) -> bool:
        source = getattr(event, "source", None) or {}
        relates = (source.get("content") or {}).get("m.relates_to") or {}
        replied = (relates.get("m.in_reply_to") or {}).get("event_id")
        return bool(replied and replied in self._own_events)

    def _is_mentioned(self, event: Any) -> bool:
        """Whether we were tagged via the client's @-mention UI (MSC3952).

        This is the reliable signal: a mention pill can land anywhere in the
        message, and clients are not required to prepend our display name as
        plain text, so string-prefix matching alone misses real mentions.
        """
        source = getattr(event, "source", None) or {}
        mentions = (source.get("content") or {}).get("m.mentions") or {}
        user_ids = mentions.get("user_ids") or []
        return self._policy.user_id in user_ids

    def _allow(self, sender: str) -> bool:
        limit = self._policy.max_per_user_per_minute
        if limit <= 0:
            return True
        now = time.monotonic()
        recent = [t for t in self._recent.get(sender, []) if now - t < 60.0]
        if len(recent) >= limit:
            self._recent[sender] = recent
            return False
        recent.append(now)
        self._recent[sender] = recent
        return True

    async def _send(
        self,
        room_id: str,
        body: str,
        formatted: str | None = None,
        *,
        reply_to: str | None = None,
    ) -> None:
        content: dict[str, Any] = {"msgtype": "m.notice", "body": body}
        if formatted:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = formatted
        if reply_to:
            # AC-7: thread the reply so the class timeline stays readable.
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": reply_to,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": reply_to},
            }
        response = await self._client.room_send(
            room_id, "m.room.message", content, ignore_unverified_devices=True
        )
        event_id = getattr(response, "event_id", None)
        if event_id:
            self.remember_own_message(event_id)


def _render(answer: Answer) -> tuple[str, str | None]:
    """Render an answer as (plain body, HTML formatted_body) — AC-8."""
    plain = [answer.text]
    formatted = [_markdown_to_html(answer.text)]

    if answer.citations:
        plain.append("\nQuellen:")
        formatted.append("<br/><b>Quellen:</b><ul>")
        for citation in answer.citations:
            page = f", S. {citation.page}" if citation.page else ""
            label = f"{citation.header_text}{page}"
            plain.append(
                f"[{citation.index}] {label}" + (f" – {citation.url}" if citation.url else "")
            )
            safe = html.escape(label)
            if citation.url:
                formatted.append(
                    f'<li>[{citation.index}] <a href="{html.escape(citation.url)}">{safe}</a></li>'
                )
            else:
                formatted.append(f"<li>[{citation.index}] {safe}</li>")
        formatted.append("</ul>")

    return "\n".join(plain), "".join(formatted)


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_CODE_RE = re.compile(r"`([^`]+)`")


def _markdown_to_html(text: str) -> str:
    """Minimal Markdown rendering.

    Deliberately tiny: the model produces bold, inline code and bullet lists, and a
    full Markdown dependency would buy nothing while widening the escaping surface.
    Everything is escaped first, so nothing the model emits can inject markup.
    """
    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)

    lines = escaped.split("\n")
    out: list[str] = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        bullet = re.match(r"^(?:[*\-•]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{bullet.group(1)}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if stripped:
            out.append(f"{stripped}<br/>")
    if in_list:
        out.append("</ul>")
    return "".join(out)
