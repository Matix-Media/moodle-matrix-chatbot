"""Prompt-facing aliases for PII tokens — see ``specs/013-pii-tokenization.md`` AC-30.

A token is `⟦PIIPERSON3a9f2c1e9b0d⟧`: twelve hex characters the model has no
reason to care about and every reason to get wrong. That matters because five
pipeline stages — condensing, decomposition, expansion, step-back and the
follow-up hop — ask the model to *rewrite* a question and then use its answer as
a search query. A single character of drift produces a term that matches
nothing, and the model may drop the token entirely as noise. Stages that exist
to increase recall end up decreasing it.

So prompts get `⟦PERSON_A⟧` instead. The model never has to reproduce a name or
a hash, only a short salient label, and the real token is substituted back here
from a map the model cannot corrupt.

Letters, not digits: two callers parse numbers straight out of a raw response
(rerank ordering, CRAG relevance score), and a `[0-9a-f]{12}` payload echoed
into either one injects garbage.

The map is ephemeral, per-request, in-memory, and never consulted by
``tokenize``/``detokenize``. It is a transport encoding for one round-trip, not
part of the tokenization contract — AC-2's "pure function of
`(entity_type, normalized)`" is untouched.
"""

from __future__ import annotations

import re

import structlog

from bsbot.pii.guard import LLMLike
from bsbot.pii.tokenizer import TOKEN_RE

log = structlog.get_logger(__name__)

#: Same delimiters as a real token — already known not to collide with the
#: `[QUELLE N]` citation syntax — but a short, letters-only payload.
ALIAS_RE = re.compile(r"⟦(PERSON|EMAIL)_([A-Z]+)⟧")

#: Anything the model must not be asked to reproduce verbatim, for callers that
#: parse structure (not prose) out of a response.
ENTITY_RE = re.compile(f"{TOKEN_RE.pattern}|{ALIAS_RE.pattern}")


def _letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. A roster chunk really can exceed 26 names."""
    out = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        out = chr(ord("A") + remainder) + out
    return out


class Aliaser:
    """Bidirectional token <-> alias map for a single request."""

    def __init__(self) -> None:
        self._by_token: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def alias_out(self, text: str) -> str:
        """Replace every PII token with its short alias, minting on first sight."""
        if not text or "⟦" not in text:
            return text
        return TOKEN_RE.sub(self._assign, text)

    def _assign(self, match: re.Match[str]) -> str:
        token = match.group(0)
        existing = self._by_token.get(token)
        if existing is not None:
            return existing
        kind = match.group(1)
        index = self._counters.get(kind, 0)
        self._counters[kind] = index + 1
        alias = f"⟦{kind}_{_letters(index)}⟧"
        self._by_token[token] = alias
        self._by_alias[alias] = token
        return alias

    def alias_in(self, text: str) -> str:
        """Restore real tokens, dropping any alias this request never issued.

        An alias that was never sent is the model inventing or garbling one.
        Dropping it is safe: rewrites are additive under RRF, so a query that
        lost its entity is merely weak, whereas a corrupted one is a term that
        matches nothing at all. The original question is in the query list
        either way.
        """
        if not text or "⟦" not in text:
            return text
        unknown = 0

        def restore(match: re.Match[str]) -> str:
            nonlocal unknown
            token = self._by_alias.get(match.group(0))
            if token is None:
                unknown += 1
                return ""
            return token

        result = ALIAS_RE.sub(restore, text)
        if unknown:
            log.info("pii.alias_corrupted", count=unknown)
        return result


class AliasingLLM:
    """Swaps tokens for aliases on the way out, and back again on the way in."""

    def __init__(self, inner: LLMLike, aliaser: Aliaser) -> None:
        self._inner = inner
        self._aliaser = aliaser

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        purpose: str = "answer",
    ) -> str:
        raw = self._inner.generate(
            self._aliaser.alias_out(prompt),
            system=self._aliaser.alias_out(system) if system else system,
            model=model,
            temperature=temperature,
            purpose=purpose,
        )
        return self._aliaser.alias_in(raw)


__all__ = ["ALIAS_RE", "ENTITY_RE", "Aliaser", "AliasingLLM"]
