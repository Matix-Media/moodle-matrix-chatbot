"""PII tokenization — see ``specs/013-pii-tokenization.md``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bsbot.api.pii.tokenizer import PiiStoreLike, PiiTokenizer

if TYPE_CHECKING:
    from bsbot.shared.config import Settings


def build_pii_tokenizer(settings: Settings, store: PiiStoreLike) -> PiiTokenizer | None:
    """Construct a `PiiTokenizer`, or ``None`` when PII tokenization is disabled.

    Imports spaCy lazily so a deployment that leaves ``BSBOT_PII__ENABLED``
    unset never needs the dependency installed at all (spec 013 AC-23).
    """
    if not settings.pii.enabled:
        return None
    from bsbot.api.pii.spacy_model import load_spacy_model

    return PiiTokenizer(store, nlp=load_spacy_model(settings.pii.spacy_model))


__all__ = ["PiiTokenizer", "build_pii_tokenizer"]
