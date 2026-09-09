"""HTTP client for `api`'s Q&A and mod-message-ingestion endpoints —
`matrix` never opens the SQLite Store directly, see
specs/015-microservice-split.md. Implements `bot.py`'s `PipelineLike` and
`IngestMessageLike` protocols.

Both calls block synchronously (a plain `httpx.Client`, not async) rather than
truly overlapping matrix-nio's event loop — matching how `BerufsschuleBot`
already tolerated a multi-second blocking `pipeline.answer()` call before this
existed; the work just moved from this process to `api`'s.
"""

from __future__ import annotations

import httpx

from bsbot.rag.pipeline import Answer


class ApiClient:
    def __init__(self, base_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def answer(
        self,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        room_id: str | None = None,
    ) -> Answer:
        response = self._http.post(
            "/api/ask",
            json={"question": question, "history": history},
            headers=self._headers,
        )
        response.raise_for_status()
        return Answer(**response.json())

    def ingest_message(
        self,
        *,
        room_id: str,
        event_id: str,
        sender: str,
        text: str,
        timemodified: int,
        room_name: str = "",
        max_history_per_room: int = 10,
    ) -> list[int]:
        response = self._http.post(
            "/internal/ingest-message",
            json={
                "room_id": room_id,
                "event_id": event_id,
                "sender": sender,
                "text": text,
                "timemodified": timemodified,
                "room_name": room_name,
                "max_history_per_room": max_history_per_room,
            },
            headers=self._headers,
        )
        response.raise_for_status()
        return list(response.json()["chunk_ids"])
