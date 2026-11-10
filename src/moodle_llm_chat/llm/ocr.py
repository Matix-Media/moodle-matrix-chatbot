"""Image transcription with any vision-capable chat model.

Each image is paid for once: results are cached by the SHA-256 of the image bytes
plus a model tag (the mime label never affects the key — the same bytes must hit
cache however they were labelled). The mime type *is* forwarded to the model,
since declaring WEBP bytes as PNG fails or silently misbehaves on some providers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from moodle_llm_chat.ingest.extract import OcrCallable

INSTRUCTION = (
    "Transkribiere den gesamten lesbaren Text in diesem Bild exakt und vollständig. "
    "Gib nur den Text zurück, ohne Kommentare. Behalte Überschriften, Aufzählungen und "
    "Tabellenstruktur bei. Falls das Bild keinen lesbaren Text enthält, antworte mit "
    "einem leeren Text."
)

#: Bound how long a worker thread waits on one transcription.
THREAD_TIMEOUT_S = 180.0


class OCRCache(Protocol):
    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None: ...

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None: ...


class VisionOCR:
    def __init__(self, model: BaseChatModel, cache: OCRCache, *, model_tag: str) -> None:
        self._model = model
        self._cache = cache
        self._tag = model_tag

    async def transcribe(
        self, image: bytes, *, page: int | None = None, mime_type: str = "image/png"
    ) -> str:
        digest = hashlib.sha256(image).hexdigest()
        cached = self._cache.cached_ocr(digest, self._tag)
        if cached is not None:
            return cached
        message = HumanMessage(
            content=[
                {
                    "type": "image",
                    "base64": base64.b64encode(image).decode("ascii"),
                    "mime_type": mime_type,
                },
                {"type": "text", "text": INSTRUCTION},
            ]
        )
        response = await self._model.ainvoke([message])
        text = response.text.strip()
        self._cache.cache_ocr(digest, self._tag, text)
        return text

    def threadsafe(self, loop: asyncio.AbstractEventLoop) -> OcrCallable:
        """A blocking OCR callable for ``extract()`` running in a worker thread.

        Each call is scheduled back onto ``loop``, so the cache (the store) is only
        ever touched from the thread that owns its connection.
        """

        def call(image: bytes, *, page: int | None = None, mime_type: str = "image/png") -> str:
            future = asyncio.run_coroutine_threadsafe(
                self.transcribe(image, page=page, mime_type=mime_type), loop
            )
            return future.result(timeout=THREAD_TIMEOUT_S)

        return call
