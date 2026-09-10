"""Egress guard — see ``specs/013-pii-tokenization.md`` AC-26.

Tokenization is applied by callers, at the points where they know what the text
means. That is the right place for it, but it is enforced only by discipline,
and discipline has already failed twice here: the benchmark judge sent raw
golden questions to Gemini, and follow-up suggestions came back in token space
and were never resolved.

This module is the net beneath that. It wraps the LLM at the Protocol level —
the same shape as ``eval.runner._RecordingLLM`` — so every prompt passes a cheap
deterministic check on its way out, without ``GeminiClient`` needing to know
anything about PII. Wrapping the Protocol rather than the concrete client also
keeps ``transcribe_image``/``describe_image`` out of scope, which is where the
spec's Non-goals already put them.

The check is a gazetteer of known entities, not NER: this sees fully-formatted
prompts, where a model pass would be expensive and indiscriminate. It cannot
find a name the system has never seen, so it is a safety net and not a
replacement for ``PiiTokenizer.tokenize``.
"""

from __future__ import annotations

from typing import Protocol

import structlog

from bsbot.pii.tokenizer import PiiTokenizer

log = structlog.get_logger(__name__)


class LLMLike(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = ...,
        model: str | None = ...,
        temperature: float = ...,
        purpose: str = ...,
    ) -> str: ...


class GuardedLLM:
    """Scrubs known PII out of every prompt on its way to the LLM."""

    def __init__(self, inner: LLMLike, tokenizer: PiiTokenizer) -> None:
        self._inner = inner
        self._tokenizer = tokenizer

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        purpose: str = "answer",
    ) -> str:
        prompt, caught = self._tokenizer.scrub(prompt)
        if system is not None:
            system, system_caught = self._tokenizer.scrub(system)
            caught += system_caught
        if caught:
            # Not an error the caller can act on — the payload is already clean.
            # It means some upstream path skipped tokenization, which is worth
            # finding, so name the stage that produced the prompt.
            log.warning("pii.egress_scrubbed", purpose=purpose, entities=caught)
        return self._inner.generate(
            prompt,
            system=system,
            model=model,
            temperature=temperature,
            purpose=purpose,
        )


__all__ = ["GuardedLLM", "LLMLike"]
