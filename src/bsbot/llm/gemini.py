"""Concrete Google Gemini client.

Kept deliberately thin: it adapts the SDK to the narrow Protocols the rest of the
code depends on (``EmbedAPI``, OCR callable, answer generation), so everything
upstream stays testable without network access or an API key.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog
from google import genai
from google.genai import types

from bsbot.config import GeminiConfig

log = structlog.get_logger(__name__)

#: bsbot never passes tools=, so automatic function calling has nothing to do.
#: Left at the SDK default it prints an "AFC is enabled" advisory on every single
#: call and adds pointless bookkeeping; disabling it explicitly silences that.
_NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)


class GeminiClient:
    def __init__(self, config: GeminiConfig) -> None:
        self._config = config
        self._client = genai.Client(api_key=config.api_key.get_secret_value())

    # -- embeddings ---------------------------------------------------- #

    def embed(self, *, texts: list[str], task_type: str, dim: int) -> list[list[float]]:
        response = self._client.models.embed_content(
            model=self._config.embed_model,
            contents=texts,  # type: ignore[arg-type]
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dim),
        )
        embeddings = response.embeddings or []
        return [list(e.values or []) for e in embeddings]

    # -- OCR ------------------------------------------------------------ #

    def transcribe_image(
        self, image: bytes, *, page: int | None = None, mime_type: str = "image/png"
    ) -> str:
        """Transcribe a scanned page or a standalone image attachment.

        ``mime_type`` defaults to PNG because the PDF-scan call site always
        renders real PNG bytes; a standalone image attachment must pass its
        actual detected type (spec 006 AC-24) — Gemini only accepts
        image/png, image/jpeg and image/webp, and declaring the wrong one would
        likely fail or silently misbehave. Runs on the cheap utility model and is
        cached by the caller, so each image is paid for exactly once.
        """
        instruction = (
            "Transkribiere den gesamten lesbaren Text in diesem Bild exakt und "
            "vollständig. Gib nur den Text zurück, ohne Kommentare. Behalte "
            "Überschriften, Aufzählungen und Tabellenstruktur bei. Falls das "
            "Bild keinen lesbaren Text enthält, antworte mit einem leeren Text."
        )
        parts: list[types.PartUnionDict] = [
            types.Part.from_bytes(data=image, mime_type=mime_type),
            types.Part.from_text(text=instruction),
        ]
        response = self._client.models.generate_content(
            model=self._config.utility_model,
            contents=parts,
            config=types.GenerateContentConfig(temperature=0.0, automatic_function_calling=_NO_AFC),
        )
        return (response.text or "").strip()

    def describe_image(self, image: bytes, *, mime_type: str = "image/png") -> str:
        """Analyze and describe an image/diagram/chart for semantic understanding.

        Runs on the utility model and describes visible elements, diagram flow,
        labels, and meaning so it can be indexed and searched semantically.
        """
        instruction = (
            "Beschreibe dieses Bild oder Diagramm präzise für Schüler und eine semantische Suche. "
            "Erkläre, was dargestellt ist (z.B. Architektur, Ablauf, Netzwerk, UML, Tabelle), "
            "benenne alle sichtbaren Komponenten, Schritte und Beschriftungen. "
            "Fasse die Bedeutung kurz und strukturiert zusammen."
        )
        parts: list[types.PartUnionDict] = [
            types.Part.from_bytes(data=image, mime_type=mime_type),
            types.Part.from_text(text=instruction),
        ]
        response = self._client.models.generate_content(
            model=self._config.utility_model,
            contents=parts,
            config=types.GenerateContentConfig(temperature=0.2, automatic_function_calling=_NO_AFC),
        )
        return (response.text or "").strip()

    # -- generation ----------------------------------------------------- #

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int | None = None,
        purpose: str = "answer",
    ) -> str:
        """Generate text.

        ``purpose`` labels the call ("answer", "expand", "rerank") so logs and
        metrics can tell which stage of the pipeline is slow or failing.
        """
        config: dict[str, Any] = {
            "temperature": temperature,
            "automatic_function_calling": _NO_AFC,
        }
        if system:
            config["system_instruction"] = system
        if max_output_tokens:
            config["max_output_tokens"] = max_output_tokens
        chosen = model or self._config.answer_model
        response = self._client.models.generate_content(
            model=chosen,
            contents=prompt,
            config=types.GenerateContentConfig(**config),
        )
        log.debug("gemini.generate", purpose=purpose, model=chosen)
        return (response.text or "").strip()


class CachingOcr:
    """OCR and AI vision understanding with a persistent per-image cache (spec 006 AC-11).

    Supports both:
    - mode="ocr": verbatim text/table transcription
    - mode="describe": semantic explanation of diagrams, charts, and figures
    """

    def __init__(
        self,
        client: GeminiClient,
        store: Any,
        model_tag: str = "ocr-v1",
        mode: str = "ocr",
    ) -> None:
        self._client = client
        self._store = store
        self._tag = model_tag
        self._mode = mode

    def __call__(
        self, image: bytes, *, page: int | None = None, mime_type: str = "image/png"
    ) -> str:
        # The cache key is content only — the same bytes hit cache regardless of
        # what format label they arrive with.
        digest = hashlib.sha256(image).hexdigest()
        cached = self._store.cached_ocr(digest, self._tag)
        if cached is not None:
            return cached
        if self._mode == "describe":
            text = self._client.describe_image(image, mime_type=mime_type)
        else:
            text = self._client.transcribe_image(image, page=page, mime_type=mime_type)
        self._store.cache_ocr(digest, self._tag, text)
        return text
