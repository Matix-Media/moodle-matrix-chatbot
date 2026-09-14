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

#: Bump whenever ``tokenize()``'s detection semantics change in a way that
#: does not alter chunk body text — a new normalization rule, a new exclusion,
#: a model swap. Folded into the indexer's change-detection hash (spec 013
#: AC-42) alongside ``chunk.body``: since the storage flip (AC-13), body text
#: is raw and untouched by anything this module does, so a detection-logic-only
#: change is otherwise invisible to that hash and silently skipped by
#: ``--reset-all`` — found live, 65% of a real corpus's chunks kept stale
#: tokenized headers through a `--reset-all` that appeared to run cleanly.
#: Mirrors ``EXTRACT_VERSION`` in ``ingest/extract.py``, same convention:
#:   1 -> AC-40 (metadata skip) and AC-41 (digit rejection)
TOKENIZER_LOGIC_VERSION = 1

#: Matches any digit, in any script — a cheap, near-zero-false-negative signal
#: that a NER-flagged span is not a real person's name (spec 013 AC-41). A
#: live corpus audit found this covers course/class codes ("IT4bili"),
#: filenames, URLs, and — notably — literal dates, whose exact substring
#: HybridSearcher._date_search/_boost depend on surviving untouched.
_DIGIT_RE = re.compile(r"\d")


class PiiStoreLike(Protocol):
    def upsert_pii_token(
        self, token: str, entity_type: str, normalized: str, original: str
    ) -> None: ...
    def pii_original(self, token: str) -> str | None: ...
    def all_pii_tokens(self) -> dict[str, str]: ...
    def pii_normalized(self, entity_type: str) -> list[str]: ...


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
    """Case-, whitespace- and diacritic-insensitive key for a person span (AC-4).

    Diacritics are folded so `Müller` and `Muller` share one token. Without that
    the two hash differently, and since a hash is what the embedder and the LLM
    see, a student who types the name without its umlaut — routine on a phone —
    gets a different entity than the one the corpus recorded.

    Deliberately does not merge different surface forms of the same person
    ("Herr Müller" vs "Max Müller") — there is no coreference resolution
    here, see spec 013 AC-5.
    """
    collapsed = " ".join(unicodedata.normalize("NFKC", text).split())
    return fold(collapsed)


def fold(text: str) -> str:
    """Case- and diacritic-insensitive fold that preserves length.

    Every input character maps to exactly one output character, so an offset in
    the folded text is the same offset in the original. That is what lets the
    gazetteer in ``PiiTokenizer.scrub`` search in folded space ("muller") and
    still redact the right span of the untouched original ("Müller").
    """
    out: list[str] = []
    for ch in text:
        decomposed = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c)) or ch
        lowered = base[0].lower()
        out.append(lowered[0] if lowered else base[0])
    return "".join(out)


def make_token(entity_type: str, normalized: str) -> str:
    """A deterministic token: a pure function of ``(entity_type, normalized)`` (AC-2)."""
    digest = hashlib.sha256(f"{entity_type}:{normalized}".encode()).hexdigest()[:12]
    return f"⟦PII{entity_type}{digest}⟧"


class PiiTokenizer:
    """Detects and reversibly tokenizes person names and email addresses."""

    def __init__(self, store: PiiStoreLike, *, nlp: NlpLike) -> None:
        self._store = store
        self._nlp = nlp
        #: Compiled lazily and dropped whenever a new PERSON is learned, so the
        #: gazetteer never costs a SQL query plus a regex compile per call — a
        #: reindex drives `generate()` thousands of times.
        self._gazetteer: re.Pattern[str] | None = None
        self._gazetteer_names: dict[str, str] = {}

    def _remember(self, token: str, entity_type: str, normalized: str, original: str) -> None:
        self._store.upsert_pii_token(token, entity_type, normalized, original)
        if entity_type == "PERSON" and normalized not in self._gazetteer_names.values():
            self._gazetteer = None

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

    def _tokenize_span(self, text: str) -> str:
        if not text:
            return text
        text = EMAIL_RE.sub(self._replace_email, text)
        text = self._tokenize_persons(text)
        # A second pass over what NER left behind, against names already known
        # (AC-38). The German model is trained on capitalized prose, which is what
        # Moodle PDFs are — but a student's question is lowercase, terse chat
        # ("wer ist max müller"), and that is precisely where it fails. Once a name
        # has been seen anywhere in the corpus, this catches it everywhere.
        scrubbed, _ = self.scrub(text)
        return scrubbed

    def _replace_email(self, match: re.Match[str]) -> str:
        original = match.group(0)
        normalized = normalize_email(original)
        token = make_token("EMAIL", normalized)
        self._remember(token, "EMAIL", normalized, original)
        return token

    def _tokenize_persons(self, text: str) -> str:
        ents = sorted(
            (e for e in self._nlp(text).ents if e.label_ == "PER" and not _DIGIT_RE.search(e.text)),
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
            self._remember(token, "PERSON", normalized, original)
            parts.append(token)
            cursor = ent.end_char
        parts.append(text[cursor:])
        return "".join(parts)

    def _compiled_gazetteer(self) -> re.Pattern[str] | None:
        if self._gazetteer is not None:
            return self._gazetteer
        # Only multi-word names. A bare surname is often an ordinary German word
        # ("Klein", "Berg", "Neu"), and this pass runs over whole prompts —
        # including instruction templates — so a single-token gazetteer would
        # mangle unrelated text. "max müller" as a phrase carries no such risk.
        # Digit-bearing entries are excluded too (AC-41) — defensively, since a
        # digit-bearing PERSON is never accepted going forward (_tokenize_persons
        # already filters it), but a deployment's `pii_tokens` table can still
        # hold rows written before that filter existed.
        names = [
            n for n in self._store.pii_normalized("PERSON") if " " in n and not _DIGIT_RE.search(n)
        ]
        if not names:
            return None
        self._gazetteer_names = {fold(n): n for n in names}
        # Longest first so the fullest available name wins an overlap.
        alternation = "|".join(
            re.escape(f) for f in sorted(self._gazetteer_names, key=len, reverse=True)
        )
        self._gazetteer = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")
        return self._gazetteer

    def scrub(self, text: str) -> tuple[str, int]:
        """Last-resort redaction for text about to leave for Gemini (AC-26).

        Deliberately *not* NER: this runs on fully-formatted prompts, where a
        model pass would be both expensive and indiscriminate. It is a cheap
        deterministic net for the cases NER structurally misses — a name spaCy
        failed to tag in a terse lowercase question, a name an LLM reintroduced
        while rewriting, a call site that forgot to tokenize at all. It catches
        only entities already known from `pii_tokens`, so it cannot replace
        `tokenize()`; it is the net beneath it.

        Returns the scrubbed text and how many entities it caught — a non-zero
        count means something upstream failed to tokenize and is worth logging.
        """
        if not text:
            return text, 0
        caught = 0

        def replace_email(match: re.Match[str]) -> str:
            nonlocal caught
            caught += 1
            original = match.group(0)
            normalized = normalize_email(original)
            token = make_token("EMAIL", normalized)
            self._remember(token, "EMAIL", normalized, original)
            return token

        text = EMAIL_RE.sub(replace_email, text)

        pattern = self._compiled_gazetteer()
        if pattern is None:
            return text, caught

        folded = fold(text)
        parts: list[str] = []
        cursor = 0
        for match in pattern.finditer(folded):
            normalized = self._gazetteer_names.get(match.group(0))
            if normalized is None:  # pragma: no cover - alternation is built from the keys
                continue
            parts.append(text[cursor : match.start()])
            parts.append(make_token("PERSON", normalized))
            cursor = match.end()
            caught += 1
        parts.append(text[cursor:])
        return "".join(parts), caught

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
    "TOKENIZER_LOGIC_VERSION",
    "TOKEN_RE",
    "DocLike",
    "EntLike",
    "NlpLike",
    "PiiStoreLike",
    "PiiTokenizer",
    "fold",
    "make_token",
    "normalize_email",
    "normalize_person",
]
