"""Build LangChain chat and embedding models from one provider-neutral config.

The rest of the code only ever sees ``BaseChatModel`` / ``Embeddings``. Everything
provider-specific — parameter names, how "thinking" is switched on, which knobs a
provider rejects — is decided here, in :func:`chat_model_kwargs`, which is a pure
function so the mapping can be tested without constructing anything.

Thinking levels (``off|low|medium|high``) map to:

* **gemini** — ``thinking_budget`` (1k / 8k / 24k tokens, or the explicit budget) plus
  ``include_thoughts`` so thought summaries stream. ``off`` leaves the model default.
* **anthropic** — current Claude models use *adaptive* thinking steered by
  ``output_config.effort``; ``budget_tokens`` and sampling parameters are rejected
  (400), and Opus 5.5 cannot disable thinking at all. So every level, including
  ``off`` (→ ``low``), is an effort level, and no ``temperature`` is ever sent.
* **openai** — hosted: Responses API with ``reasoning={effort, summary}`` so summaries
  stream; an OpenAI-compatible server (``base_url`` set) gets plain
  ``reasoning_effort`` on chat completions. ``temperature`` is dropped when reasoning.
* **ollama** — ``reasoning=True|False`` (the ``think`` flag).

``thinking_params`` (raw provider kwargs) are merged last, for servers this table
does not know. Utility calls (titles, OCR) never think.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from moodle_llm_chat.config import (
    EMBEDDING_PROVIDERS,
    EmbeddingConfig,
    LLMConfig,
    Provider,
    ThinkingLevel,
)

#: LangChain's ``model_provider`` names.
_LANGCHAIN_PROVIDER = {
    Provider.GEMINI: "google_genai",
    Provider.OPENAI: "openai",
    Provider.OLLAMA: "ollama",
    Provider.ANTHROPIC: "anthropic",
}

_GEMINI_BUDGETS = {
    ThinkingLevel.LOW: 1024,
    ThinkingLevel.MEDIUM: 8192,
    ThinkingLevel.HIGH: 24576,
}

#: Room for adaptive thinking plus a full answer. Anthropic needs an explicit cap.
ANTHROPIC_MAX_TOKENS = 32_000

EmbeddingTask = Literal["query", "document"]


def chat_model_kwargs(cfg: LLMConfig, *, utility: bool = False) -> tuple[str, dict[str, Any]]:
    """Return ``(langchain_provider, constructor_kwargs)`` for ``cfg``."""
    provider = cfg.provider
    thinking = ThinkingLevel.OFF if utility else cfg.thinking
    kwargs: dict[str, Any] = {"model": cfg.utility_model if utility else cfg.model}
    api_key = cfg.api_key.get_secret_value() if cfg.api_key else None

    if provider is Provider.GEMINI:
        kwargs["api_key"] = api_key
        kwargs["temperature"] = cfg.temperature
        if thinking is not ThinkingLevel.OFF:
            kwargs["thinking_budget"] = cfg.thinking_budget_tokens or _GEMINI_BUDGETS[thinking]
            kwargs["include_thoughts"] = True

    elif provider is Provider.ANTHROPIC:
        kwargs["api_key"] = api_key
        kwargs["max_tokens"] = ANTHROPIC_MAX_TOKENS
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if thinking is ThinkingLevel.OFF:
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["reasoning_effort"] = thinking.value
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

    elif provider is Provider.OPENAI:
        if api_key:
            kwargs["api_key"] = api_key
        elif cfg.base_url:
            # The client insists on some key; local servers ignore it.
            kwargs["api_key"] = "not-needed"
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        kwargs["stream_usage"] = True
        if thinking is ThinkingLevel.OFF:
            kwargs["temperature"] = cfg.temperature
        elif cfg.base_url:
            kwargs["reasoning_effort"] = thinking.value
        else:
            kwargs["reasoning"] = {"effort": thinking.value, "summary": "auto"}
            kwargs["use_responses_api"] = True
            # Standard content blocks: reasoning arrives as ``reasoning`` blocks.
            kwargs["output_version"] = "v1"

    elif provider is Provider.OLLAMA:
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        kwargs["temperature"] = cfg.temperature
        kwargs["reasoning"] = thinking is not ThinkingLevel.OFF

    if cfg.thinking_params and not utility:
        kwargs.update(cfg.thinking_params)
    return _LANGCHAIN_PROVIDER[provider], kwargs


def build_chat_model(cfg: LLMConfig, *, utility: bool = False) -> BaseChatModel:
    """Construct the chat model. Nothing is contacted until the first call."""
    provider, kwargs = chat_model_kwargs(cfg, utility=utility)
    model = kwargs.pop("model")
    built = init_chat_model(model, model_provider=provider, **kwargs)
    assert isinstance(built, BaseChatModel)
    return built


def build_embeddings(cfg: EmbeddingConfig, *, task: EmbeddingTask) -> Embeddings:
    """Construct an embeddings client for queries or for documents.

    Gemini produces deliberately different vectors per task type; using one type for
    both measurably weakens retrieval, so callers build one instance per task.
    """
    if cfg.provider not in EMBEDDING_PROVIDERS:
        raise ValueError(f"{cfg.provider.value} has no embeddings API")
    api_key = cfg.api_key.get_secret_value() if cfg.api_key else None

    if cfg.provider is Provider.GEMINI:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(
            model=cfg.model,
            api_key=cfg.api_key,
            task_type="RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT",
            output_dimensionality=cfg.dim,
        )
    if cfg.provider is Provider.OPENAI:
        from langchain_openai import OpenAIEmbeddings

        if cfg.base_url:
            return OpenAIEmbeddings(
                model=cfg.model,
                base_url=cfg.base_url,
                api_key=api_key or "not-needed",  # type: ignore[arg-type]
                check_embedding_ctx_length=False,
            )
        return OpenAIEmbeddings(
            model=cfg.model,
            api_key=api_key,  # type: ignore[arg-type]
            dimensions=cfg.dim,
        )
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=cfg.model, base_url=cfg.base_url)
