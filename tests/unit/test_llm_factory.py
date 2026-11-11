"""Provider factory: one config shape → each provider's native chat/embedding class.

Built offline (no network), against the real LangChain classes, so a renamed
constructor parameter in a provider package fails here and not in production.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from moodle_llm_chat.config import (
    EmbeddingConfig,
    LLMConfig,
    Provider,
    ThinkingLevel,
)
from moodle_llm_chat.llm.factory import (
    build_chat_model,
    build_embeddings,
    chat_model_kwargs,
)


def _llm(provider: Provider, **overrides: Any) -> LLMConfig:
    base: dict[str, Any] = {
        "provider": provider,
        "model": "main-model",
        "utility_model": "small-model",
        "api_key": SecretStr("k") if provider is not Provider.OLLAMA else None,
        "base_url": None,
        "temperature": 0.0,
        "max_tool_calls": 25,
        "thinking": ThinkingLevel.OFF,
        "thinking_budget_tokens": None,
        "thinking_params": None,
    }
    base.update(overrides)
    return LLMConfig(**base)


def _emb(provider: Provider, **overrides: Any) -> EmbeddingConfig:
    base: dict[str, Any] = {
        "provider": provider,
        "model": "embed-model",
        "dim": 768,
        "api_key": SecretStr("k") if provider is not Provider.OLLAMA else None,
        "base_url": None,
        "batch_size": 100,
        "rpm": 90,
        "items_per_minute": 2500,
    }
    base.update(overrides)
    return EmbeddingConfig(**base)


class TestChatModelClasses:
    @pytest.mark.parametrize(
        ("provider", "cls"),
        [
            (Provider.GEMINI, ChatGoogleGenerativeAI),
            (Provider.OPENAI, ChatOpenAI),
            (Provider.OLLAMA, ChatOllama),
            (Provider.ANTHROPIC, ChatAnthropic),
        ],
    )
    def test_each_provider_builds_its_native_class(
        self, provider: Provider, cls: type[Any]
    ) -> None:
        model = build_chat_model(_llm(provider))
        assert isinstance(model, cls)

    def test_utility_flag_picks_the_utility_model(self) -> None:
        model = build_chat_model(_llm(Provider.OLLAMA), utility=True)
        assert isinstance(model, ChatOllama)
        assert model.model == "small-model"

    def test_base_url_is_passed_to_openai_compatible_servers(self) -> None:
        model = build_chat_model(
            _llm(Provider.OPENAI, api_key=None, base_url="http://localhost:1234/v1")
        )
        assert isinstance(model, ChatOpenAI)
        assert model.openai_api_base == "http://localhost:1234/v1"

    def test_ollama_base_url(self) -> None:
        model = build_chat_model(_llm(Provider.OLLAMA, base_url="http://gpu:11434"))
        assert isinstance(model, ChatOllama)
        assert model.base_url == "http://gpu:11434"

    def test_models_stream_usage(self) -> None:
        """Quotas meter tokens from usage metadata; OpenAI only reports it when asked."""
        model = build_chat_model(_llm(Provider.OPENAI))
        assert isinstance(model, ChatOpenAI)
        assert model.stream_usage is True


class TestThinkingMapping:
    def test_gemini_off_sends_no_thinking_params(self) -> None:
        _, kwargs = chat_model_kwargs(_llm(Provider.GEMINI))
        assert "thinking_budget" not in kwargs
        assert kwargs.get("include_thoughts") is not True

    @pytest.mark.parametrize(
        ("level", "budget"),
        [(ThinkingLevel.LOW, 1024), (ThinkingLevel.MEDIUM, 8192), (ThinkingLevel.HIGH, 24576)],
    )
    def test_gemini_levels_map_to_budgets_and_stream_thoughts(
        self, level: ThinkingLevel, budget: int
    ) -> None:
        model = build_chat_model(_llm(Provider.GEMINI, thinking=level))
        assert isinstance(model, ChatGoogleGenerativeAI)
        assert model.thinking_budget == budget
        assert model.include_thoughts is True

    def test_gemini_explicit_budget_wins(self) -> None:
        _, kwargs = chat_model_kwargs(
            _llm(Provider.GEMINI, thinking=ThinkingLevel.LOW, thinking_budget_tokens=3000)
        )
        assert kwargs["thinking_budget"] == 3000

    def test_anthropic_uses_adaptive_thinking_with_effort_never_budget(self) -> None:
        """Current Claude models reject budget_tokens and sampling parameters (400)."""
        _, kwargs = chat_model_kwargs(
            _llm(Provider.ANTHROPIC, thinking=ThinkingLevel.HIGH, thinking_budget_tokens=9000)
        )
        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert kwargs["reasoning_effort"] == "high"
        assert "budget_tokens" not in str(kwargs)
        assert "temperature" not in kwargs

    def test_anthropic_off_lowers_effort_instead_of_disabling(self) -> None:
        """Opus 5.5 cannot disable thinking; 'off' means the lowest effort."""
        _, kwargs = chat_model_kwargs(_llm(Provider.ANTHROPIC))
        assert kwargs["reasoning_effort"] == "low"
        assert "temperature" not in kwargs
        assert kwargs.get("thinking", {}).get("type") != "disabled"

    def test_anthropic_model_builds_with_thinking(self) -> None:
        model = build_chat_model(_llm(Provider.ANTHROPIC, thinking=ThinkingLevel.MEDIUM))
        assert isinstance(model, ChatAnthropic)
        assert model.reasoning_effort == "medium"
        assert model.max_tokens is not None and model.max_tokens >= 16_000

    def test_openai_hosted_reasoning_uses_responses_api_with_summaries(self) -> None:
        model = build_chat_model(_llm(Provider.OPENAI, thinking=ThinkingLevel.MEDIUM))
        assert isinstance(model, ChatOpenAI)
        assert model.reasoning == {"effort": "medium", "summary": "auto"}
        assert model.use_responses_api is True
        # Reasoning models reject a temperature other than the default.
        assert model.temperature is None

    def test_openai_compatible_server_gets_plain_reasoning_effort(self) -> None:
        """vLLM / LM Studio speak chat completions, not the Responses API."""
        _, kwargs = chat_model_kwargs(
            _llm(
                Provider.OPENAI,
                api_key=None,
                base_url="http://localhost:8000/v1",
                thinking=ThinkingLevel.LOW,
            )
        )
        assert kwargs["reasoning_effort"] == "low"
        assert "use_responses_api" not in kwargs

    def test_openai_off_keeps_temperature(self) -> None:
        _, kwargs = chat_model_kwargs(_llm(Provider.OPENAI, temperature=0.2))
        assert kwargs["temperature"] == 0.2
        assert "reasoning" not in kwargs

    def test_ollama_reasoning_flag(self) -> None:
        on = build_chat_model(_llm(Provider.OLLAMA, thinking=ThinkingLevel.HIGH))
        off = build_chat_model(_llm(Provider.OLLAMA))
        assert isinstance(on, ChatOllama) and isinstance(off, ChatOllama)
        assert on.reasoning is True
        assert off.reasoning is False

    def test_thinking_params_override_derived_values(self) -> None:
        _, kwargs = chat_model_kwargs(
            _llm(
                Provider.OLLAMA,
                thinking=ThinkingLevel.HIGH,
                thinking_params={"reasoning": "high"},
            )
        )
        assert kwargs["reasoning"] == "high"

    def test_utility_calls_never_think(self) -> None:
        _, kwargs = chat_model_kwargs(
            _llm(Provider.OLLAMA, thinking=ThinkingLevel.HIGH), utility=True
        )
        assert kwargs["reasoning"] is False
        _, kwargs = chat_model_kwargs(
            _llm(Provider.GEMINI, thinking=ThinkingLevel.HIGH), utility=True
        )
        assert "thinking_budget" not in kwargs


class TestEmbeddings:
    def test_gemini_query_and_document_task_types(self) -> None:
        query = build_embeddings(_emb(Provider.GEMINI), task="query")
        doc = build_embeddings(_emb(Provider.GEMINI), task="document")
        assert isinstance(query, GoogleGenerativeAIEmbeddings)
        assert isinstance(doc, GoogleGenerativeAIEmbeddings)
        assert query.task_type == "RETRIEVAL_QUERY"
        assert doc.task_type == "RETRIEVAL_DOCUMENT"
        assert query.output_dimensionality == 768

    def test_openai_dimensions(self) -> None:
        emb = build_embeddings(_emb(Provider.OPENAI, dim=256), task="document")
        assert isinstance(emb, OpenAIEmbeddings)
        assert emb.dimensions == 256

    def test_openai_compatible_server_skips_tiktoken_length_checks(self) -> None:
        """Non-OpenAI models are not tokenized by tiktoken; sending token ids breaks them."""
        emb = build_embeddings(
            _emb(Provider.OPENAI, api_key=None, base_url="http://localhost:8000/v1"),
            task="document",
        )
        assert isinstance(emb, OpenAIEmbeddings)
        assert emb.check_embedding_ctx_length is False
        assert emb.openai_api_base == "http://localhost:8000/v1"

    def test_openai_compatible_server_omits_dimensions_by_default(self) -> None:
        """Some servers (vLLM on a non-Matryoshka model) reject the parameter outright."""
        emb = build_embeddings(
            _emb(Provider.OPENAI, base_url="http://localhost:8000/v1", dim=1024),
            task="document",
        )
        assert isinstance(emb, OpenAIEmbeddings)
        assert emb.dimensions is None

    def test_openai_compatible_server_sends_dimensions_when_opted_in(self) -> None:
        """OpenRouter or vLLM serving a Matryoshka model can truncate to the configured dim."""
        emb = build_embeddings(
            _emb(
                Provider.OPENAI,
                base_url="https://openrouter.ai/api/v1",
                dim=1024,
                send_dimensions=True,
            ),
            task="document",
        )
        assert isinstance(emb, OpenAIEmbeddings)
        assert emb.dimensions == 1024
        assert emb.check_embedding_ctx_length is False

    def test_ollama(self) -> None:
        emb = build_embeddings(_emb(Provider.OLLAMA, base_url="http://gpu:11434"), task="query")
        assert isinstance(emb, OllamaEmbeddings)
        assert emb.base_url == "http://gpu:11434"

    def test_anthropic_rejected(self) -> None:
        with pytest.raises(ValueError, match="embeddings"):
            build_embeddings(_emb(Provider.ANTHROPIC), task="query")
