"""``ChatOpenAI`` for OpenAI-compatible servers that also keeps the reasoning they send.

LangChain's ``ChatOpenAI`` drops every non-standard field of a chat-completions
response, and reasoning is non-standard there. The servers this project talks to put
it in one of three places, per streamed ``delta`` (or per non-streamed ``message``):

* ``reasoning_content`` (string): DeepSeek, vLLM, LM Studio, llama.cpp, SGLang.
* ``reasoning`` (string): OpenRouter, newer vLLM and LM Studio, Groq.
* ``reasoning_details`` (list): OpenRouter's structured form, with ``reasoning.text``,
  ``reasoning.summary`` and ``reasoning.encrypted`` entries. Only used when no string
  form is present, because OpenRouter usually sends both with the same text.

Whatever is found lands in ``additional_kwargs["reasoning_content"]`` (the key Ollama's
integration uses too); the agent runner reads it from there.
"""

from __future__ import annotations

from typing import Any

import openai
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

#: ``reasoning_details`` entry type → the field holding its readable text.
_DETAIL_TEXT = {"reasoning.text": "text", "reasoning.summary": "summary"}


def reasoning_text(payload: dict[str, Any]) -> str:
    """The reasoning in one ``delta`` / ``message`` dict, or ``""``."""
    for key in ("reasoning_content", "reasoning"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    details = payload.get("reasoning_details")
    if not isinstance(details, list):
        return ""
    parts = []
    for detail in details:
        if isinstance(detail, dict) and (field := _DETAIL_TEXT.get(str(detail.get("type")))):
            text = detail.get(field)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class ChatOpenAICompatible(ChatOpenAI):
    """Chat completions against an OpenAI-compatible server, reasoning included."""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if choices and (text := reasoning_text(choices[0].get("delta") or {})):
            generation.message.additional_kwargs["reasoning_content"] = text
        return generation

    def _create_chat_result(
        self,
        response: dict[str, Any] | openai.BaseModel,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, raw.get("choices") or [], strict=False):
            if text := reasoning_text(choice.get("message") or {}):
                generation.message.additional_kwargs["reasoning_content"] = text
        return result
