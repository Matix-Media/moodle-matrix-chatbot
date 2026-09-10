"""Verifies spec 013 — PII tokenization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bsbot.config import Settings
from bsbot.export import export_all
from bsbot.index.search import HybridSearcher, SearchHit
from bsbot.index.store import Store
from bsbot.ingest.fetcher import FetchResult
from bsbot.ingest.indexer import Indexer
from bsbot.ingest.model import ContentItem, ContentKind
from bsbot.pii import build_pii_tokenizer
from bsbot.pii.tokenizer import (
    PATH_SEP,
    PiiTokenizer,
    make_token,
    normalize_email,
    normalize_person,
)
from bsbot.rag.pipeline import AnswerPipeline

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def item(doc_id: str = "a", **kw: Any) -> ContentItem:
    defaults: dict[str, Any] = dict(
        doc_id=doc_id,
        course_id=1,
        course_name="LF05",
        section_name="Sec",
        module_id=1,
        module_name="Mod",
        modname="resource",
        title="T",
        kind=ContentKind.INLINE,
        header_path=["LF05", "Sec", "Mod"],
        timemodified=100,
    )
    return ContentItem(**{**defaults, **kw})


class _Ent:
    def __init__(self, text: str, start: int, end: int) -> None:
        self.text = text
        self.label_ = "PER"
        self.start_char = start
        self.end_char = end


class _Doc:
    def __init__(self, ents: list[_Ent]) -> None:
        self.ents = ents


class FakeNlp:
    """Tags every occurrence of a configured name as a PER entity."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def __call__(self, text: str) -> _Doc:
        ents: list[_Ent] = []
        for name in self._names:
            start = text.find(name)
            if start != -1:
                ents.append(_Ent(name, start, start + len(name)))
        return _Doc(ents)


class FakePiiStore:
    """An in-memory ``PiiStoreLike`` — first-seen ``original`` casing wins."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def upsert_pii_token(
        self, token: str, entity_type: str, normalized: str, original: str
    ) -> None:
        self._map.setdefault(token, original)

    def pii_original(self, token: str) -> str | None:
        return self._map.get(token)

    def all_pii_tokens(self) -> dict[str, str]:
        return dict(self._map)


def hit(chunk_id: int, text: str, **kw: Any) -> SearchHit:
    defaults = dict(
        chunk_id=chunk_id,
        doc_id=f"d{chunk_id}",
        text=text,
        header_text="LF05 › Sec › Mod",
        course_name="LF05",
        module_name="Mod",
        module_url=f"https://m.example/mod/page/view.php?id={chunk_id}",
        title="Skript",
        page=None,
        score=0.5,
    )
    return SearchHit(**{**defaults, **kw})


class FakeSearcher:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 8, room_id: str | None = None) -> list[SearchHit]:
        self.queries.append(query)
        return self._hits[:limit]

    def neighbors(self, chunk_id: int, *, radius: int) -> list[SearchHit]:
        return []


class FakeLLM:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.prompts: list[tuple[str, str]] = []

    def generate(
        self, prompt: str, *, system: str | None = None, purpose: str = "answer", **kw: Any
    ) -> str:
        self.prompts.append((purpose, prompt))
        return self._responses.get(purpose, "OK. [1]")


class SpyLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        purpose: str = "answer",
    ) -> str:
        self.prompts.append(prompt)
        return {"hype": "Frage 1?\nFrage 2?", "contextualize": "Kontext."}.get(purpose, "")


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "index.db", embed_dim=2) as s:
        yield s


# --------------------------------------------------------------------------- #
# Token format, normalization, idempotency
# --------------------------------------------------------------------------- #


class TestTokenFormat:
    def test_email_tokenizes_to_expected_format(self) -> None:
        """AC-1"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        result = tok.tokenize("Kontakt: mueller@schule.de")
        assert result != "Kontakt: mueller@schule.de"
        import re

        assert re.search(r"⟦PIIEMAIL[0-9a-f]{12}⟧", result)

    def test_malformed_email_with_a_short_tld_is_still_tokenized(self) -> None:
        """Regression: a real Moodle module title was found containing
        'marlon.heyser@itech-bs14.d...' — broken at the source (Moodle itself),
        not truncated by this codebase. A strict "valid TLD" regex would refuse
        to match this and let a real name+domain reach the LLM untokenized."""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        result = tok.tokenize("Modulverantwortliche/r: marlon.heyser@itech-bs14.d...")
        assert "marlon.heyser@itech-bs14" not in result
        assert "⟦PIIEMAIL" in result

    def test_person_tokenizes_to_expected_format(self) -> None:
        """AC-1"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp(["Herr Mueller"]))
        result = tok.tokenize("Bitte wende dich an Herr Mueller.")
        import re

        assert re.search(r"⟦PIIPERSON[0-9a-f]{12}⟧", result)

    def test_same_normalized_entity_always_produces_same_token(self) -> None:
        """AC-2: a pure function of (entity_type, normalized) — no shared state needed."""
        t1 = make_token("EMAIL", normalize_email("Mueller@Schule.DE"))
        t2 = make_token("EMAIL", normalize_email("mueller@schule.de"))
        assert t1 == t2

        # Two independent tokenizers with independent stores must still agree.
        tok1 = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        tok2 = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        assert tok1.tokenize("a@b.de") == tok2.tokenize("a@b.de")

    def test_email_normalizes_case_insensitively(self) -> None:
        """AC-3"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        assert tok.tokenize("Teacher@Schule.DE") == tok.tokenize("teacher@schule.de")

    def test_person_normalizes_case_and_whitespace_insensitively(self) -> None:
        """AC-4"""
        store_ = FakePiiStore()
        names = ["Max Müller", "MAX MÜLLER", "  Max   Müller  "]
        tokens = {PiiTokenizer(store_, nlp=FakeNlp([n.strip()])).tokenize(n) for n in names}
        # Each produces exactly one distinct tokenized string, but the *token*
        # embedded in each must be identical.
        import re

        found = {re.search(r"⟦PIIPERSON[0-9a-f]{12}⟧", t).group(0) for t in tokens}  # type: ignore[union-attr]
        assert len(found) == 1

    def test_different_surface_forms_of_same_person_are_not_merged(self) -> None:
        """AC-5: no coreference resolution — this is a documented limitation."""
        t1 = make_token("PERSON", normalize_person("Herr Müller"))
        t2 = make_token("PERSON", normalize_person("Max Müller"))
        assert t1 != t2

    def test_retokenizing_already_tokenized_text_is_a_no_op(self) -> None:
        """AC-6"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp(["Herr Mueller"]))
        once = tok.tokenize("Kontakt: Herr Mueller, mueller@schule.de")
        twice = tok.tokenize(once)
        assert once == twice

    def test_text_without_pii_is_unchanged(self) -> None:
        """AC-7"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        assert tok.tokenize("Die Prüfung ist am 15.03.2026.") == "Die Prüfung ist am 15.03.2026."
        assert tok.tokenize("") == ""


class TestTokenizePath:
    """`tokenize_path` (breadcrumb segments tokenized together, not in isolation).

    Regression coverage for false-positive PERSON detections on ordinary
    course/section/module names — a lone breadcrumb segment ("Lernfeld 10", a
    team name) is a short, context-free fragment that spaCy's NER judges much
    less reliably than the same text read together with its neighbors.
    """

    def test_empty_path_returns_empty_list(self) -> None:
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        assert tok.tokenize_path([]) == []

    def test_segments_without_pii_pass_through_unchanged(self) -> None:
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        assert tok.tokenize_path(["LF05", "Sec", "Mod"]) == ["LF05", "Sec", "Mod"]

    def test_a_name_spanning_one_segment_is_tokenized_in_place(self) -> None:
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp(["Herr Mueller"]))
        result = tok.tokenize_path(["LF05", "Sec", "Kontakt Herr Mueller"])
        assert result[0] == "LF05"
        assert result[1] == "Sec"
        assert "Herr Mueller" not in result[2]
        assert "⟦PIIPERSON" in result[2]

    def test_isolated_fragment_false_positive_is_avoided_with_sibling_context(self) -> None:
        """A fake NER that only (wrongly) flags a bare fragment when it is the
        *entire* input — standing in for spaCy's real tendency to misjudge a
        short, context-free noun phrase read on its own. Tokenizing the whole
        breadcrumb in one pass gives the segment real neighbors and avoids the
        false positive that per-segment tokenization would produce."""

        class FlagsOnlyWhenAlone:
            def __init__(self, flagged: str) -> None:
                self._flagged = flagged

            def __call__(self, text: str) -> _Doc:
                if text.strip() == self._flagged:
                    return _Doc([_Ent(text, 0, len(text))])
                return _Doc([])

        nlp = FlagsOnlyWhenAlone("Lernfeldverantwortliche")
        tok_isolated = PiiTokenizer(FakePiiStore(), nlp=nlp)
        assert "⟦PIIPERSON" in tok_isolated.tokenize("Lernfeldverantwortliche")

        tok_path = PiiTokenizer(FakePiiStore(), nlp=nlp)
        result = tok_path.tokenize_path(["Lernfeld 10", "Lernfeldverantwortliche", "Mod"])
        assert result == ["Lernfeld 10", "Lernfeldverantwortliche", "Mod"]

    def test_falls_back_per_segment_when_the_split_misaligns(self) -> None:
        """A detected span that swallows the join separator must not corrupt
        the result list — the count of returned segments must always match
        the count of input segments."""

        class SpansTheSeparator:
            def __call__(self, text: str) -> _Doc:
                sep = PATH_SEP
                if sep in text:
                    start = text.index(sep)
                    end = min(start + len(sep) + 3, len(text))
                    return _Doc([_Ent(text[start:end], start, end)])
                return _Doc([])

        tok = PiiTokenizer(FakePiiStore(), nlp=SpansTheSeparator())
        result = tok.tokenize_path(["AAA", "BBB", "CCC"])
        assert len(result) == 3


# --------------------------------------------------------------------------- #
# Reversible mapping
# --------------------------------------------------------------------------- #


class TestDetokenize:
    def test_detokenize_restores_original_values(self) -> None:
        """AC-8"""
        store_ = FakePiiStore()
        tok = PiiTokenizer(store_, nlp=FakeNlp(["Herr Mueller"]))
        tokenized = tok.tokenize("Kontakt: Herr Mueller, mueller@schule.de")
        restored = tok.detokenize(tokenized)
        assert restored == "Kontakt: Herr Mueller, mueller@schule.de"

    def test_unmatched_token_is_left_as_is(self) -> None:
        """AC-9"""
        tok = PiiTokenizer(FakePiiStore(), nlp=FakeNlp([]))
        text = "⟦PIIPERSONdeadbeef0000⟧ war nicht da."
        assert tok.detokenize(text) == text

    def test_first_seen_casing_wins_on_detokenize(self) -> None:
        """AC-10"""
        store_ = FakePiiStore()
        token = make_token("EMAIL", normalize_email("a@b.de"))
        store_.upsert_pii_token(token, "EMAIL", normalize_email("a@b.de"), "A@B.de")
        store_.upsert_pii_token(token, "EMAIL", normalize_email("a@b.de"), "different@b.de")
        tok = PiiTokenizer(store_, nlp=FakeNlp([]))
        assert tok.detokenize(token) == "A@B.de"


class TestStorePiiTokens:
    """AC-11: the mapping is persisted in the index store, not a separate file."""

    def test_upsert_and_read_back(self, store: Store) -> None:
        store.upsert_pii_token("t1", "EMAIL", "a@b.de", "A@B.de", now=100)
        assert store.pii_original("t1") == "A@B.de"

    def test_first_seen_original_never_overwritten(self, store: Store) -> None:
        store.upsert_pii_token("t1", "EMAIL", "a@b.de", "A@B.de", now=100)
        store.upsert_pii_token("t1", "EMAIL", "a@b.de", "different@b.de", now=200)
        assert store.pii_original("t1") == "A@B.de"

    def test_missing_token_returns_none(self, store: Store) -> None:
        assert store.pii_original("nope") is None

    def test_all_pii_tokens_returns_full_mapping(self, store: Store) -> None:
        store.upsert_pii_token("t1", "EMAIL", "a@b.de", "a@b.de")
        store.upsert_pii_token("t2", "PERSON", "max", "Max")
        assert store.all_pii_tokens() == {"t1": "a@b.de", "t2": "Max"}

    def test_mapping_survives_reopening_the_store(self, tmp_path: Path) -> None:
        path = tmp_path / "index.db"
        with Store(path) as s:
            s.upsert_pii_token("t1", "EMAIL", "a@b.de", "a@b.de")
        with Store(path) as s:
            assert s.pii_original("t1") == "a@b.de"


# --------------------------------------------------------------------------- #
# Ingest-time protection
# --------------------------------------------------------------------------- #


class _NoFetch:
    async def fetch(self, url: str, *, moodle_timemodified: int | None = None) -> FetchResult:
        raise AssertionError("inline text must never trigger a fetch")


class TestIndexerProtection:
    async def test_llm_never_sees_raw_pii(self, store: Store) -> None:
        """AC-12"""
        store.persist_crawl([item(text="Kontakt: Herr Mueller, mueller@schule.de")])
        llm = SpyLLM()
        tok = PiiTokenizer(store, nlp=FakeNlp(["Herr Mueller"]))
        stats = await Indexer(
            store, _NoFetch(), llm=llm, hype=True, contextualize=True, pii_tokenizer=tok
        ).index_pending()  # type: ignore[arg-type]

        assert stats.indexed == 1
        assert llm.prompts  # the augmentation calls did fire
        for prompt in llm.prompts:
            assert "mueller@schule.de" not in prompt
            assert "Herr Mueller" not in prompt

    async def test_stored_chunk_text_is_tokenized(self, store: Store) -> None:
        """AC-13"""
        store.persist_crawl([item(text="Kontakt: Herr Mueller, mueller@schule.de")])
        tok = PiiTokenizer(store, nlp=FakeNlp(["Herr Mueller"]))
        await Indexer(store, _NoFetch(), pii_tokenizer=tok).index_pending()  # type: ignore[arg-type]

        chunk_text = store.chunks_for("a")[0].text
        assert "mueller@schule.de" not in chunk_text
        assert "Herr Mueller" not in chunk_text
        assert "⟦PIIEMAIL" in chunk_text
        assert "⟦PIIPERSON" in chunk_text

    async def test_raw_document_row_is_left_untouched(self, store: Store) -> None:
        """AC-13: `documents.text` stays raw, so local export is unaffected."""
        store.persist_crawl([item(text="Kontakt: mueller@schule.de")])
        tok = PiiTokenizer(store, nlp=FakeNlp([]))
        await Indexer(store, _NoFetch(), pii_tokenizer=tok).index_pending()  # type: ignore[arg-type]

        assert store.document("a").text == "Kontakt: mueller@schule.de"


class TestMatrixMessageProtection:
    def test_message_text_is_tokenized_before_storing(self, store: Store) -> None:
        """AC-14"""
        tok = PiiTokenizer(store, nlp=FakeNlp([]))
        store.index_matrix_message(
            room_id="!r:example.org",
            event_id="$1",
            sender="@teacher:example.org",
            text="Meine E-Mail: mueller@schule.de",
            timemodified=100,
            pii_tokenizer=tok,
        )
        doc_id = "matrix:!r:example.org:$1"
        assert "mueller@schule.de" not in (store.document(doc_id).text or "")
        assert "⟦PIIEMAIL" in (store.document(doc_id).text or "")
        # The sender identifier itself is never tokenized.
        chunks = store.chunks_for(doc_id)
        assert chunks[0].meta["sender"] == "@teacher:example.org"


class TestHydrateBreadcrumb:
    def test_course_module_title_are_tokenized(self, store: Store) -> None:
        """AC-16"""
        store.persist_crawl([item(course_name="LF05 Herr Mueller", module_name="Skript Mueller")])
        store.replace_chunks(
            "a", [("Inhalt zum Thema.", {"ordinal": 0})], header_text="LF05 Herr Mueller"
        )
        tok = PiiTokenizer(store, nlp=FakeNlp(["Herr Mueller"]))
        searcher = HybridSearcher(store, embedder=None, pii_tokenizer=tok)

        hits = searcher.search("Inhalt", limit=5)

        assert hits
        assert "Mueller" not in hits[0].course_name
        assert "⟦PIIPERSON" in hits[0].course_name


# --------------------------------------------------------------------------- #
# Query-time protection and answer detokenization
# --------------------------------------------------------------------------- #


class TestPipeline:
    def test_question_and_context_reach_the_llm_only_as_tokens(self) -> None:
        """AC-15, AC-17, AC-18"""
        store_ = FakePiiStore()
        email_token = make_token("EMAIL", normalize_email("mueller@schule.de"))
        person_token = make_token("PERSON", normalize_person("Herr Mueller"))
        store_.upsert_pii_token(
            email_token, "EMAIL", normalize_email("mueller@schule.de"), "mueller@schule.de"
        )
        store_.upsert_pii_token(
            person_token, "PERSON", normalize_person("Herr Mueller"), "Herr Mueller"
        )
        tok = PiiTokenizer(store_, nlp=FakeNlp(["Herr Mueller"]))

        llm = FakeLLM(responses={"answer": f"Die E-Mail von {person_token} ist {email_token}. [1]"})
        h = hit(
            1,
            f"Kontakt: {email_token}",
            course_name=person_token,
            header_text=f"{person_token} › Sprechstunde",
            title=person_token,
        )
        pipeline = AnswerPipeline(
            FakeSearcher([h]), llm, expand=False, rerank=False, pii_tokenizer=tok
        )

        answer = pipeline.answer("Wie erreiche ich Herr Mueller?")

        answer_prompt = next(p for kind, p in llm.prompts if kind == "answer")
        assert "Herr Mueller" not in answer_prompt
        assert "mueller@schule.de" not in answer_prompt
        assert person_token in answer_prompt
        assert email_token in answer_prompt

        assert "mueller@schule.de" in answer.text
        assert "Herr Mueller" in answer.text
        assert answer.citations
        assert answer.citations[0].course_name == "Herr Mueller"
        assert "Herr Mueller" in answer.citations[0].header_text

    def test_actionable_fallback_uses_the_raw_question(self) -> None:
        """AC-19: a local URL, never sent to Gemini, must not be tokenized."""
        store_ = FakePiiStore()
        tok = PiiTokenizer(store_, nlp=FakeNlp(["Herr Mueller"]))
        llm = FakeLLM()
        pipeline = AnswerPipeline(
            FakeSearcher([]),
            llm,
            expand=False,
            rerank=False,
            crag=True,
            pii_tokenizer=tok,
            moodle_base_url="https://m.example",
        )

        answer = pipeline.answer("Wo ist Herr Mueller?")

        assert "Mueller" in answer.text
        assert "⟦PII" not in answer.text

    def test_suggest_followup_receives_tokenized_text_not_the_final_answer(self) -> None:
        """AC-20: a regression test for the gap found during plan verification —
        this call must never receive the already-detokenized `Answer.text`."""
        store_ = FakePiiStore()
        email_token = make_token("EMAIL", normalize_email("mueller@schule.de"))
        store_.upsert_pii_token(
            email_token, "EMAIL", normalize_email("mueller@schule.de"), "mueller@schule.de"
        )
        tok = PiiTokenizer(store_, nlp=FakeNlp([]))
        llm = FakeLLM(
            responses={
                "answer": f"Die E-Mail ist {email_token}. [1]",
                "suggest_followup": "Wann ist Sprechstunde?",
            }
        )
        h = hit(1, f"Kontakt: {email_token}")
        pipeline = AnswerPipeline(
            FakeSearcher([h]),
            llm,
            expand=False,
            rerank=False,
            suggest_followup=True,
            pii_tokenizer=tok,
        )

        pipeline.answer("Wie ist die E-Mail?")

        followup_prompt = next(p for kind, p in llm.prompts if kind == "suggest_followup")
        assert "mueller@schule.de" not in followup_prompt
        assert email_token in followup_prompt


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


class TestExport:
    def test_inline_text_export_is_unaffected(self, tmp_path: Path) -> None:
        """AC-21: `documents.text` is never tokenized in storage, so this is a no-op."""
        with Store(tmp_path / "index.db") as store_:
            store_.persist_crawl([item(text="Kontakt: mueller@schule.de")])
            tok = PiiTokenizer(store_, nlp=FakeNlp([]))
            export_all(
                store_,
                output_dir=tmp_path / "out",
                combined_txt_path=tmp_path / "combined.txt",
                combined_md_path=tmp_path / "combined.md",
                pii_tokenizer=tok,
            )
        combined = (tmp_path / "combined.txt").read_text()
        assert "mueller@schule.de" in combined

    def test_chunk_sourced_export_is_detokenized(self, tmp_path: Path) -> None:
        """AC-22"""
        with Store(tmp_path / "index.db") as store_:
            email_token = make_token("EMAIL", normalize_email("mueller@schule.de"))
            store_.upsert_pii_token(
                email_token, "EMAIL", normalize_email("mueller@schule.de"), "mueller@schule.de"
            )
            store_.persist_crawl([item()])
            store_.replace_chunks(
                "a",
                [(f"Kontakt: {email_token}", {"body": f"Kontakt: {email_token}"})],
                header_text="X",
            )
            tok = PiiTokenizer(store_, nlp=FakeNlp([]))
            export_all(
                store_,
                output_dir=tmp_path / "out",
                combined_txt_path=tmp_path / "combined.txt",
                combined_md_path=tmp_path / "combined.md",
                pii_tokenizer=tok,
            )
        combined = (tmp_path / "combined.txt").read_text()
        assert "mueller@schule.de" in combined
        assert email_token not in combined


# --------------------------------------------------------------------------- #
# Configuration and rollout
# --------------------------------------------------------------------------- #


class TestConfig:
    def test_disabled_by_default(self) -> None:
        """AC-23"""
        assert Settings().pii.enabled is False

    def test_build_pii_tokenizer_returns_none_when_disabled(self) -> None:
        """AC-23: no spaCy import when the flag is off."""
        assert build_pii_tokenizer(Settings(), FakePiiStore()) is None


class TestRollout:
    def test_reset_extraction_for_all_documents(self, store: Store) -> None:
        """AC-24"""
        store.persist_crawl([item("a"), item("b")])
        store.record_extraction("a", text_sha256="x", extract_version=1)
        store.record_extraction("b", text_sha256="y", extract_version=1)
        assert store.documents_needing_extraction(extract_version=1) == []

        count = store.reset_extraction_for_all_documents()

        assert count == 2
        pending = store.documents_needing_extraction(extract_version=1)
        assert {d.doc_id for d in pending} == {"a", "b"}
