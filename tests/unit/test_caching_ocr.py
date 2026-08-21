"""Verifies ``CachingOcr`` forwards the detected mime type through to Gemini,
and caches purely by image content — the format label must never affect the
cache key, only what gets sent to the API.
"""

from __future__ import annotations

from bsbot.llm.gemini import CachingOcr


class FakeGeminiClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe_image(self, image: bytes, *, page=None, mime_type: str = "image/png") -> str:
        self.calls.append({"image": image, "page": page, "mime_type": mime_type})
        return f"transcribed ({mime_type})"


class FakeStore:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], str] = {}

    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
        return self._cache.get((image_sha256, model_tag))

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None:
        self._cache[(image_sha256, model_tag)] = text


class TestMimeTypeForwarding:
    def test_default_mime_type_is_png(self) -> None:
        client = FakeGeminiClient()
        ocr = CachingOcr(client, FakeStore())  # type: ignore[arg-type]
        ocr(b"image-bytes", page=1)
        assert client.calls[0]["mime_type"] == "image/png"

    def test_explicit_mime_type_is_forwarded(self) -> None:
        client = FakeGeminiClient()
        ocr = CachingOcr(client, FakeStore())  # type: ignore[arg-type]
        ocr(b"image-bytes", mime_type="image/webp")
        assert client.calls[0]["mime_type"] == "image/webp"


class TestCaching:
    def test_mime_type_is_not_part_of_the_cache_key(self) -> None:
        """The same bytes must hit cache regardless of what format they're called
        with — the cache key is content, not the label."""
        client = FakeGeminiClient()
        store = FakeStore()
        ocr = CachingOcr(client, store)  # type: ignore[arg-type]
        ocr(b"same-bytes", mime_type="image/png")
        ocr(b"same-bytes", mime_type="image/webp")
        assert len(client.calls) == 1
