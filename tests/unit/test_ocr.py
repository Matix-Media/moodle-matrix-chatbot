"""VisionOCR: provider-neutral image transcription with a per-image cache."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from moodle_llm_chat.llm.ocr import VisionOCR


class RecordingModel(GenericFakeChatModel):
    seen: ClassVar[list[list[BaseMessage]]] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        type(self).seen.append(messages)
        return super()._generate(messages, *args, **kwargs)


def _model(*answers: str) -> RecordingModel:
    RecordingModel.seen = []
    return RecordingModel(messages=iter([AIMessage(content=a) for a in answers]))


class DictCache:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
        return self.data.get((image_sha256, model_tag))

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None:
        self.data[(image_sha256, model_tag)] = text


class TestTranscribe:
    async def test_sends_image_block_with_mime_type_and_instruction(self) -> None:
        ocr = VisionOCR(_model("  Seite 1 Text  "), DictCache(), model_tag="m")
        text = await ocr.transcribe(b"\x89PNGbytes", mime_type="image/webp")
        assert text == "Seite 1 Text"
        (messages,) = RecordingModel.seen
        (human,) = [m for m in messages if isinstance(m, HumanMessage)]
        blocks = human.content
        assert isinstance(blocks, list)
        image = next(b for b in blocks if isinstance(b, dict) and b.get("type") == "image")
        assert image["mime_type"] == "image/webp"
        assert image["base64"]
        assert any(isinstance(b, dict) and b.get("type") == "text" for b in blocks)

    async def test_default_mime_type_is_png(self) -> None:
        ocr = VisionOCR(_model("x"), DictCache(), model_tag="m")
        await ocr.transcribe(b"bytes")
        (messages,) = RecordingModel.seen
        human = next(m for m in messages if isinstance(m, HumanMessage))
        assert isinstance(human.content, list)
        image = next(b for b in human.content if isinstance(b, dict) and b["type"] == "image")
        assert image["mime_type"] == "image/png"


class TestCaching:
    async def test_same_bytes_are_transcribed_once_regardless_of_mime_label(self) -> None:
        cache = DictCache()
        ocr = VisionOCR(_model("first", "second"), cache, model_tag="m")
        a = await ocr.transcribe(b"same", mime_type="image/png")
        b = await ocr.transcribe(b"same", mime_type="image/webp")
        assert a == b == "first"
        assert len(RecordingModel.seen) == 1

    async def test_cache_is_scoped_to_model_tag(self) -> None:
        cache = DictCache()
        await VisionOCR(_model("a"), cache, model_tag="m1").transcribe(b"img")
        await VisionOCR(_model("b"), cache, model_tag="m2").transcribe(b"img")
        assert sorted(cache.data.values()) == ["a", "b"]


class TestThreadBridge:
    async def test_callable_from_worker_thread_runs_on_the_loop(self) -> None:
        """extract() runs in a thread; the cache (the store) must only be touched on
        the loop thread that owns the database connection."""
        loop_thread: list[int] = []

        class ThreadCheckingCache(DictCache):
            def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
                import threading

                loop_thread.append(threading.get_ident())
                return super().cached_ocr(image_sha256, model_tag)

        import threading

        main = threading.get_ident()
        ocr = VisionOCR(_model("from thread"), ThreadCheckingCache(), model_tag="m")
        call = ocr.threadsafe(asyncio.get_running_loop())
        result = await asyncio.to_thread(call, b"img", page=3, mime_type="image/png")
        assert result == "from thread"
        assert loop_thread == [main]
