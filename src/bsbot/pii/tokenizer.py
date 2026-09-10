"""PII tokenization — see ``specs/013-pii-tokenization.md``.

Replaces person names and email addresses with stable, deterministic tokens
before text reaches an embedding call, an LLM prompt, or the on-disk search
index. The token is a pure function of ``(entity_type, normalized_text)``, so
the same real-world entity always produces the same token whether it was seen
at indexing time or in a student's question — no shared state is needed to
keep the two in agreement. The reversible mapping lives in the caller-supplied
store and is consulted only to detokenize, never to decide what to tokenize.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

#: A practical (not full RFC 5322) email pattern — matches the rest of this
#: codebase's preference for pragmatic regexes over exhaustive ones (AC-1).
#: The TLD is deliberately `{1,}`, not the stricter `{2,}` a "valid email"
#: pattern would use: real Moodle content has genuinely malformed addresses
#: (a module titled "...marlon.heyser@itech-bs14.d..." — found live, the title
#: itself is broken at the source, not truncated by anything in this
#: codebase). A `{2,}` TLD would refuse to match the local-part+domain here
#: and let it reach the LLM raw. Since the "@" is what actually signals PII
#: here, not the TLD's validity, being lenient on the TLD costs essentially
#: no false-positive risk.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{1,}")

#: ⟦/⟧ (U+27E6/U+27E7) do not occur in Moodle content and do not collide with
#: the ``[QUELLE N]`` citation syntax elsewhere in the RAG pipeline.
TOKEN_RE = re.compile(r"⟦PII(PERSON|EMAIL)([0-9a-f]{12})⟧")

#: Separator used by :meth:`PiiTokenizer.tokenize_path` to join breadcrumb
#: segments before running NER once over the whole path. Matches the
#: separator already used to *render* a breadcrumb (``header_text`` in
#: ``store.py``/``indexer.py``), which is already relied on elsewhere as not
#: occurring in real Moodle course/section/module names.
PATH_SEP = " › "


class PiiStoreLike(Protocol):
    def upsert_pii_token(
        self, token: str, entity_type: str, normalized: str, original: str
    ) -> None: ...
    def pii_original(self, token: str) -> str | None: ...
    def all_pii_tokens(self) -> dict[str, str]: ...


class EntLike(Protocol):
    text: str
    label_: str
    start_char: int
    end_char: int


class DocLike(Protocol):
    ents: Sequence[EntLike]


class NlpLike(Protocol):
    def __call__(self, text: str) -> DocLike: ...


def normalize_email(text: str) -> str:
    """Case-insensitive key for an email address (AC-3)."""
    return text.strip().lower()


def normalize_person(text: str) -> str:
    """Case- and whitespace-insensitive key for a person span (AC-4).

    Deliberately does not merge different surface forms of the same person
    ("Herr Müller" vs "Max Müller") — there is no coreference resolution
    here, see spec 013 AC-5.
    """
    collapsed = " ".join(unicodedata.normalize("NFKC", text).split())
    return collapsed.casefold()


def make_token(entity_type: str, normalized: str) -> str:
    """A deterministic token: a pure function of ``(entity_type, normalized)`` (AC-2)."""
    digest = hashlib.sha256(f"{entity_type}:{normalized}".encode()).hexdigest()[:12]
    return f"⟦PII{entity_type}{digest}⟧"


class PiiTokenizer:
    """Detects and reversibly tokenizes person names and email addresses."""

    def __init__(self, store: PiiStoreLike, *, nlp: NlpLike) -> None:
        self._store = store
        self._nlp = nlp

    def tokenize(self, text: str) -> str:
        """Replace every email then person span with its deterministic token.

        Idempotent (AC-6): an existing ``⟦PII...⟧`` token is located first and
        left untouched, so neither the email regex nor the NER pass ever runs
        against a token's own bracketed payload — re-tokenizing already
        -tokenized text is a no-op, and untouched text with no PII passes
        through unchanged (AC-7).
        """
        if not text:
            return text
        parts: list[str] = []
        cursor = 0
        for m in TOKEN_RE.finditer(text):
            parts.append(self._tokenize_span(text[cursor : m.start()]))
            parts.append(m.group(0))
            cursor = m.end()
        parts.append(self._tokenize_span(text[cursor:]))
        return "".join(parts)

    def tokenize_path(self, parts: Sequence[str]) -> list[str]:
        """Tokenize breadcrumb-style path segments (course › section › module)
        together instead of one call per segment.

        A lone breadcrumb segment is typically a 1-4 word fragment with no
        sentence structure ("Lernfeld 10", a course code, a team name) — spaCy's
        statistical NER is trained on running prose and is unreliable on bare,
        context-free noun phrases, and German capitalizes every noun, so the
        capitalization cue that helps in English carries no signal here either.
        In practice this made ordinary course/category names get tokenized as
        PERSON. Joining the segments into one string and running NER once gives
        the model real neighboring context — closer to the prose it was trained
        on — before the result is split back into per-segment values.
        """
        if not parts:
            return list(parts)
        tokenized = self.tokenize(PATH_SEP.join(parts))
        result = tokenized.split(PATH_SEP)
        if len(result) != len(parts):
            # A detected span crossed a separator (or a part contained one) and
            # the split no longer lines up 1:1 with the input — fall back to
            # tokenizing each part in isolation rather than misalign the list.
            return [self.tokenize(p) for p in parts]
        return result

    def _tokenize_span(self, text: str) -> str:
        if not text:
            return text
        text = EMAIL_RE.sub(self._replace_email, text)
        return self._tokenize_persons(text)

    def _replace_email(self, match: re.Match[str]) -> str:
        original = match.group(0)
        normalized = normalize_email(original)
        token = make_token("EMAIL", normalized)
        self._store.upsert_pii_token(token, "EMAIL", normalized, original)
        return token

    def _tokenize_persons(self, text: str) -> str:
        ents = sorted(
            (e for e in self._nlp(text).ents if e.label_ == "PER"),
            key=lambda e: e.start_char,
        )
        if not ents:
            return text
        parts: list[str] = []
        cursor = 0
        for ent in ents:
            if ent.start_char < cursor:
                continue  # overlapping/out-of-order span; skip defensively
            parts.append(text[cursor : ent.start_char])
            original = ent.text.strip()
            normalized = normalize_person(original)
            token = make_token("PERSON", normalized)
            self._store.upsert_pii_token(token, "PERSON", normalized, original)
            parts.append(token)
            cursor = ent.end_char
        parts.append(text[cursor:])
        return "".join(parts)

    def detokenize(self, text: str) -> str:
        """Resolve every token back to its stored original value (AC-8/AC-9)."""
        if not text or "⟦" not in text:
            return text
        mapping = self._store.all_pii_tokens()
        if not mapping:
            return text
        return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


__all__ = [
    "EMAIL_RE",
    "PATH_SEP",
    "TOKEN_RE",
    "DocLike",
    "EntLike",
    "NlpLike",
    "PiiStoreLike",
    "PiiTokenizer",
    "make_token",
    "normalize_email",
    "normalize_person",
]
