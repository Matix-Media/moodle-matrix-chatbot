"""Request/response shapes for the web chat API — see specs/014-web-chat.md."""

from __future__ import annotations

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    history: list[tuple[str, str]] | None = None
