"""Regression: the SDK prints an "AFC is enabled" advisory on every call.

bsbot never passes ``tools=`` to Gemini, so automatic function calling is dead
weight — nothing but console noise and unnecessary SDK bookkeeping on every
request. It should be explicitly disabled rather than left at the SDK default.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import SecretStr

from bsbot.config import GeminiConfig
from bsbot.llm.gemini import GeminiClient


def make_client() -> tuple[GeminiClient, MagicMock]:
    config = GeminiConfig(
        api_key=SecretStr("test-key"),
        answer_model="answer-model",
        utility_model="utility-model",
        embed_model="embed-model",
        embed_dim=768,
        embed_batch_size=100,
        embed_rpm=90,
        embed_items_per_minute=2500,
    )
    client = GeminiClient(config)
    mock_models = MagicMock()
    mock_models.generate_content.return_value = SimpleNamespace(text="ok")
    # genai.Client.models is a read-only property; replace the whole client object
    # our adapter holds rather than trying to patch through it.
    client._client = SimpleNamespace(models=mock_models)  # type: ignore[attr-defined]
    return client, mock_models


class TestAutomaticFunctionCallingDisabled:
    def test_generate_disables_afc(self) -> None:
        client, mock_models = make_client()
        client.generate("prompt")
        config = mock_models.generate_content.call_args.kwargs["config"]
        assert config.automatic_function_calling is not None
        assert config.automatic_function_calling.disable is True

    def test_transcribe_image_disables_afc(self) -> None:
        client, mock_models = make_client()
        client.transcribe_image(b"fake-png-bytes")
        config = mock_models.generate_content.call_args.kwargs["config"]
        assert config.automatic_function_calling is not None
        assert config.automatic_function_calling.disable is True


class TestTranscribeImageMimeType:
    """Regression: the OCR call must declare the actual image format, not a
    hardcoded 'image/png' — WEBP bytes mislabelled as PNG would likely fail or
    silently misbehave. Gemini officially supports png/jpeg/webp as image inputs.
    """

    def test_defaults_to_png_for_backward_compatibility(self) -> None:
        """PDF-page OCR always renders real PNG bytes and never passes mime_type
        explicitly; the default must keep that call site correct."""
        client, mock_models = make_client()
        client.transcribe_image(b"fake-png-bytes")
        parts = mock_models.generate_content.call_args.kwargs["contents"]
        image_part = parts[0]
        assert image_part.inline_data.mime_type == "image/png"

    def test_webp_is_declared_correctly(self) -> None:
        client, mock_models = make_client()
        client.transcribe_image(b"fake-webp-bytes", mime_type="image/webp")
        parts = mock_models.generate_content.call_args.kwargs["contents"]
        image_part = parts[0]
        assert image_part.inline_data.mime_type == "image/webp"

    def test_jpeg_is_declared_correctly(self) -> None:
        client, mock_models = make_client()
        client.transcribe_image(b"fake-jpeg-bytes", mime_type="image/jpeg")
        parts = mock_models.generate_content.call_args.kwargs["contents"]
        image_part = parts[0]
        assert image_part.inline_data.mime_type == "image/jpeg"
