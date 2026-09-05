"""Lazy spaCy model loading for PII tokenization.

``spacy`` is imported only inside :func:`load_spacy_model`, and that function
is only called when PII tokenization is enabled (``BSBOT_PII__ENABLED=true``)
— this keeps the base install free of a dependency most deployments never
need (spec 013 AC-23).
"""

from __future__ import annotations

from typing import cast

from bsbot.pii.tokenizer import NlpLike


def load_spacy_model(model_name: str) -> NlpLike:
    """Load a spaCy pipeline, presented as this package's `NlpLike` interface.

    Returning `NlpLike` rather than `spacy.language.Language` keeps the exact
    shape of the real spaCy model — and its dependency — out of every other
    module's type surface; `PiiTokenizer` only ever needs the narrower
    contract. The cast is a type-boundary adapter, not a workaround: spaCy's
    `Language.__call__` genuinely satisfies `NlpLike` at runtime (called with
    a single `str`, returns a `Doc` whose `.ents` are `Span`s with
    `text`/`label_`/`start_char`/`end_char`) — mypy's structural check on
    `Callable` protocols is just stricter than that runtime contract, given
    `Language.__call__`'s wider signature (extra optional kwargs, a `str |
    Doc` input type).
    """
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(
            "PII tokenization is enabled (BSBOT_PII__ENABLED=true) but spaCy is "
            "not installed. Run: pip install -e '.[pii]'"
        ) from exc
    try:
        return cast(NlpLike, spacy.load(model_name))
    except OSError as exc:
        raise RuntimeError(
            f"PII tokenization is enabled but the spaCy model {model_name!r} is "
            f"not downloaded. Run: python -m spacy download {model_name}"
        ) from exc


__all__ = ["load_spacy_model"]
