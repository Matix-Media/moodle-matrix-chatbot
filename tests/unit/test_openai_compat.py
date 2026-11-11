"""Reasoning from OpenAI-compatible servers, in every place they put it."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

from moodle_llm_chat.llm.openai_compat import ChatOpenAICompatible, reasoning_text


def model() -> ChatOpenAICompatible:
    return ChatOpenAICompatible(model="m", api_key="k", base_url="http://llm.test/v1")  # type: ignore[arg-type]


class TestReasoningText:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"reasoning_content": "a"}, "a"),
            ({"reasoning": "b"}, "b"),
            ({"reasoning_content": "", "reasoning": "c"}, "c"),
            (
                {"reasoning_details": [{"type": "reasoning.text", "text": "d"}]},
                "d",
            ),
            (
                {"reasoning_details": [{"type": "reasoning.summary", "summary": "e"}]},
                "e",
            ),
            # Encrypted reasoning has nothing readable.
            ({"reasoning_details": [{"type": "reasoning.encrypted", "data": "x"}]}, ""),
            ({"content": "answer"}, ""),
            ({"reasoning": None, "reasoning_details": None}, ""),
        ],
    )
    def test_fields(self, payload: dict[str, Any], expected: str) -> None:
        assert reasoning_text(payload) == expected

    def test_string_wins_over_details_with_the_same_text(self) -> None:
        """OpenRouter sends both: counting both would show every thought twice."""
        payload = {
            "reasoning": "f",
            "reasoning_details": [{"type": "reasoning.text", "text": "f"}],
        }
        assert reasoning_text(payload) == "f"


class TestModel:
    def test_streamed_chunk_keeps_reasoning(self) -> None:
        chunk = {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning": "hm"}}],
        }
        generation = model()._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
        assert generation is not None
        assert generation.message.additional_kwargs["reasoning_content"] == "hm"

    def test_usage_only_chunk_is_untouched(self) -> None:
        chunk = {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        generation = model()._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
        assert generation is not None
        assert "reasoning_content" not in generation.message.additional_kwargs

    def test_whole_response_keeps_reasoning(self) -> None:
        response = {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Antwort.",
                        "reasoning_content": "Erst nachdenken.",
                    },
                }
            ],
        }
        result = model()._create_chat_result(response)
        message = result.generations[0].message
        assert message.content == "Antwort."
        assert message.additional_kwargs["reasoning_content"] == "Erst nachdenken."
