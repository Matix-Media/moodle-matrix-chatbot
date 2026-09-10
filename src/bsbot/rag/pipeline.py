"""Answer pipeline — see ``specs/008-answering.md``.

    question -> expand -> retrieve -> fuse -> rerank -> answer -> cite

Every optional stage degrades rather than fails: if expansion or reranking hits a
quota, the pipeline continues with what it has. The only hard stop is having no
retrieved context at all, in which case we refuse without calling the model, since
a model given nothing can only invent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from bsbot.index.search import SearchHit, reciprocal_rank_fusion
from bsbot.pii.alias import ENTITY_RE, Aliaser, AliasingLLM
from bsbot.pii.tokenizer import PiiTokenizer
from bsbot.rag.prompts import (
    ANSWER_TEMPLATE,
    COMPRESS_CONTEXT_TEMPLATE,
    CONDENSE_QUESTION_TEMPLATE,
    CRAG_EVALUATE_TEMPLATE,
    DECOMPOSE_TEMPLATE,
    EXPAND_TEMPLATE,
    FOLLOWUP_TEMPLATE,
    REFUSAL_MARKER,
    RERANK_TEMPLATE,
    RERANK_TEMPLATE_DATED,
    STEP_BACK_TEMPLATE,
    SUGGEST_FOLLOWUP_TEMPLATE,
    SYSTEM_PROMPT,
)

log = structlog.get_logger(__name__)


DEFAULT_MAX_CONTEXT_CHUNKS = 6
DEFAULT_CANDIDATES = 12
DEFAULT_EXPANSIONS = 2
#: How deep each query variant is fetched before cross-query fusion runs. Wider
#: than DEFAULT_CANDIDATES on purpose: a document ranking just outside the old
#: cutoff for every individual query — the real failure this fixes — needs to
#: actually be *in* the pool before the Lernfeld boost or diversification can
#: do anything with it.
DEFAULT_PER_QUERY_LIMIT = 30
#: How many chunks from the same document may occupy the final candidate list.
#: Found on the live corpus: three chunks of one file filled half the top-12,
#: crowding out a different, relevant document.
DEFAULT_MAX_PER_DOCUMENT = 2
MAX_CHUNK_CHARS = 1800
#: How many chunks on each side of a retrieved one get spliced into its citation.
#: Found live: a Blockplan is one continuous document arbitrarily cut into
#: fixed-size chunks, so the chunk covering the actually-asked-about week can rank
#: far outside the retrieval window while the chunk right next to it doesn't —
#: 2 covers "next/previous week" for the schedule case this was built for, and
#: costs nothing when a document has no useful neighbours (nothing else nearby).
DEFAULT_NEIGHBOR_RADIUS = 2
#: Ceiling on a citation's total spliced size, independent of radius — merging
#: five 1800-char chunks into one 9000-char wall of text would starve the other
#: candidates of context budget.
MAX_EXPANDED_CHUNK_CHARS = 3600
NO_ANSWER_TEXT = "Dazu habe ich leider keine Informationen im Moodle gefunden."
_SCHOOL_TZ = ZoneInfo("Europe/Berlin")
_WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_STRIP_CHARS = " -–—•\t\r\n0123456789.)"


class SearcherLike(Protocol):
    def search(
        self, query: str, *, limit: int = 12, room_id: str | None = None
    ) -> list[SearchHit]: ...
    def neighbors(self, chunk_id: int, *, radius: int) -> list[SearchHit]: ...


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


#: The alias-wrapped LLM for the request currently being answered. A ContextVar
#: rather than a temporary attribute swap: ``AnswerPipeline`` is a singleton
#: reused across requests (see ``web.app``), and ``answer()`` is called
#: synchronously from an async route. That serializes today only because nothing
#: inside it yields — an implicit invariant a threadpool move would break
#: silently, with one request's alias map answering another's prompts.
_REQUEST_LLM: ContextVar[LLMLike | None] = ContextVar("bsbot_request_llm", default=None)


class Citation(BaseModel):
    index: int
    title: str
    course_name: str
    header_text: str
    url: str | None
    page: int | None


class Answer(BaseModel):
    text: str
    citations: list[Citation] = []
    grounded: bool = False
    error: str | None = None
    used_queries: list[str] = []
    suggested_questions: list[str] = []
    #: doc_id of every chunk that actually reached the answer prompt, in context
    #: order — i.e. after fuse/boost/diversify/rerank, not just something some query
    #: variant happened to surface. Populated even on refusal (empty then). Exists
    #: for retrieval-quality measurement (see `bsbot.eval`); has no effect on answering.
    context_doc_ids: list[str] = []


class AnswerPipeline:
    def __init__(
        self,
        searcher: SearcherLike,
        llm: LLMLike,
        *,
        expand: bool = True,
        rerank: bool = True,
        followup: bool = True,
        decompose: bool = False,
        step_back: bool = False,
        compress_context: bool = False,
        crag: bool = False,
        #: Gives the reranker today's date and each candidate's own "Stand" date, so
        #: it can tell "the chunk for this week" from "the chunk for six weeks ago"
        #: instead of reordering by topical similarity alone. Without this, reranking
        #: can (and, measured on this corpus, does) undo `_boost`'s date match —
        #: the reranker has no way to know which near-duplicate Blockplan chunk is
        #: "today's" once it only sees bare excerpts. Only takes effect when
        #: ``rerank`` is also on.
        dated_rerank: bool = False,
        #: Corrective RAG: score every candidate's relevance to the question
        #: (0.0-1.0, see `_evaluate_relevance`) and drop the ones below
        #: ``crag_filter_threshold`` before they reach reranking/context — one LLM
        #: call per candidate, so it is the most expensive technique here. Distinct
        #: from ``crag``, which only swaps the refusal *text* and touches nothing
        #: about retrieval.
        crag_filter: bool = False,
        crag_filter_threshold: float = 0.3,
        suggest_followup: bool = False,
        moodle_base_url: str | None = None,
        max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
        candidates: int = DEFAULT_CANDIDATES,
        expansions: int = DEFAULT_EXPANSIONS,
        per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
        max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
        neighbor_radius: int = DEFAULT_NEIGHBOR_RADIUS,
        utility_model: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(_SCHOOL_TZ),
        pii_tokenizer: PiiTokenizer | None = None,
    ) -> None:
        self._searcher = searcher
        self._base_llm = llm
        self._expand = expand
        self._rerank = rerank
        self._followup = followup
        self._decompose = decompose
        self._step_back = step_back
        self._compress_context = compress_context
        self._crag = crag
        self._dated_rerank = dated_rerank
        self._crag_filter = crag_filter
        self._crag_filter_threshold = crag_filter_threshold
        self._suggest_followup = suggest_followup
        self._moodle_base_url = moodle_base_url
        self._max_context = max_context_chunks
        self._candidates = candidates
        self._expansions = expansions
        self._per_query_limit = max(per_query_limit, candidates)
        self._neighbor_radius = neighbor_radius
        self._max_per_document = max_per_document
        self._utility_model = utility_model
        self._clock = clock
        self._pii_tokenizer = pii_tokenizer

    @property
    def _llm(self) -> LLMLike:
        """The LLM for the request in flight — alias-wrapped while one is.

        A property rather than a plain attribute so that every ``generate`` call
        site below picks the wrapper up without knowing it exists.
        """
        return _REQUEST_LLM.get() or self._base_llm

    def _detok(self, text: str) -> str:
        return self._pii_tokenizer.detokenize(text) if self._pii_tokenizer else text

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def answer(
        self,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        room_id: str | None = None,
    ) -> Answer:
        """Answer one question, with a fresh alias map bound for its duration."""
        if self._pii_tokenizer is None:
            return self._answer(question, history=history, room_id=room_id)
        bound = _REQUEST_LLM.set(AliasingLLM(self._base_llm, Aliaser()))
        try:
            return self._answer(question, history=history, room_id=room_id)
        finally:
            _REQUEST_LLM.reset(bound)

    def _answer(
        self,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        room_id: str | None = None,
    ) -> Answer:
        question = (question or "").strip()
        log.info("rag.question", question=question, room_id=room_id)

        # PII tokenization (spec 013): a name/email in the student's own question
        # must never reach the LLM either — everything from here on (condense,
        # decompose, expand, step-back, embedding, rerank, the final answer
        # prompt) works on the tokenized question. `raw_question` is kept only
        # for local, never-sent-to-Gemini uses (the Moodle-search fallback URL).
        raw_question = question
        if self._pii_tokenizer is not None:
            question = self._pii_tokenizer.tokenize(question)

        search_question = question
        if history and question:
            search_question = self._condense_question(question, history)

        queries = [search_question] if search_question else []
        if question and search_question != question and question not in queries:
            queries.append(question)

        if search_question and self._decompose:
            decomposed = self._decomposed_queries(search_question)
            for sub_q in decomposed:
                if sub_q not in queries:
                    queries.append(sub_q)

        if search_question and self._expand:
            expanded: list[str] = []
            for q in list(queries):
                expanded.extend(self._expanded_queries(q))
            for eq in expanded:
                if eq not in queries:
                    queries.append(eq)

        if search_question and self._step_back:
            step_back = self._step_back_query(search_question)
            if step_back and step_back not in queries:
                queries.append(step_back)

        hits = self._retrieve(search_question, queries, room_id=room_id)
        log.info(
            "rag.retrieved",
            queries=queries,
            hits=len(hits),
            headers=[h.header_text for h in hits[: self._max_context]],
        )
        if not hits:
            # A local URL, never sent to Gemini — must reflect the student's
            # literal wording, not the tokenized search query.
            fallback_text = (
                self._actionable_fallback(raw_question)
                if (self._crag and search_question)
                else NO_ANSWER_TEXT
            )
            result = Answer(text=fallback_text, grounded=False, used_queries=queries)
            log.info("rag.answered", grounded=False, citations=0)
            return result

        if self._crag_filter:
            hits = self._crag_filtered(search_question, hits)

        if self._rerank:
            hits, confident = self._reranked(search_question, hits)
            # A second, bounded hop: whenever the first pass looks thin — the
            # reranker was confident in fewer chunks than we want for context, the
            # classic shape of a question whose real terminology (a TaskCards term,
            # an abbreviation) never appeared in the original question or its
            # expansions. This runs a genuinely new query, not a re-search of the
            # same pool, so a small original hit count is not a reason to skip it —
            # if anything it is a stronger signal something is missing. One hop
            # only — the result of this second pass is never re-assessed.
            if self._followup and confident < self._max_context:
                followup_query = self._followup_query(search_question, hits[: self._max_context])
                if followup_query:
                    queries = [*queries, followup_query]
                    retried = self._retrieve(search_question, queries, room_id=room_id)
                    log.info("rag.followup", query=followup_query, hits=len(retried))
                    if retried:
                        hits, confident = self._reranked(search_question, retried)
        context_hits = hits[: self._max_context]

        history_section = ""
        if history:
            history_lines = [f"Schüler/in: {q}\nAssistent: {a}" for q, a in history[-3:]]
            history_section = "Bisheriger Gesprächsverlauf:\n" + "\n\n".join(history_lines) + "\n\n"

        prompt = ANSWER_TEMPLATE.format(
            today=_format_weekday_date(self._clock()),
            history_section=history_section,
            question=question,
            context=self._format_context(context_hits, question=search_question),
        )
        try:
            raw = self._llm.generate(prompt, system=SYSTEM_PROMPT, purpose="answer")
        except Exception as exc:
            log.warning("rag.answer_failed", error=str(exc))
            result = Answer(
                text=(
                    "Ich kann die Frage gerade nicht beantworten – der KI-Dienst "
                    "antwortet nicht. Bitte versuch es in ein paar Minuten nochmal."
                ),
                grounded=False,
                error=f"{type(exc).__name__}: {exc}",
                used_queries=queries,
                context_doc_ids=[h.doc_id for h in context_hits],
            )
            log.info("rag.answered", grounded=False, citations=0)
            return result

        result = self._finalise(
            raw, context_hits, queries, question=question, raw_question=raw_question
        )
        if self._suggest_followup and result.grounded and result.text:
            # Pass the pre-detokenization `raw` text, not `result.text` — the
            # latter has already had every PII token resolved back to a real
            # value by `_finalise`, and this call sends its `answer` argument to
            # Gemini for follow-up-question suggestions. Passing `result.text`
            # here would re-leak exactly the PII tokenization exists to protect.
            # The suggestions therefore come back in token space too, so they
            # need resolving before they reach the student — `_finalise` has
            # already run by this point and never sees them.
            result.suggested_questions = [
                self._detok(q) for q in self._suggest_followup_questions(question, raw)
            ]
        log.info("rag.answered", grounded=result.grounded, citations=len(result.citations))
        return result

    # ------------------------------------------------------------------ #

    def _condense_question(self, question: str, history: list[tuple[str, str]]) -> str:
        """Rewrite a follow-up question into a standalone search query given chat history."""
        if not history:
            return question
        history_lines = [f"Schüler/in: {q}\nAssistent: {a}" for q, a in history[-3:]]
        chat_history = "\n\n".join(history_lines)
        prompt = CONDENSE_QUESTION_TEMPLATE.format(chat_history=chat_history, question=question)
        try:
            raw = self._llm.generate(
                prompt,
                purpose="condense_question",
                model=self._utility_model,
                temperature=0.0,
            )
            condensed = raw.strip().strip("\"' ")
            if condensed:
                log.info("rag.condensed", question=question, condensed=condensed)
                return condensed
        except Exception as exc:
            log.info("rag.condense_failed", error=str(exc))
        return question

    def _suggest_followup_questions(self, question: str, answer: str) -> list[str]:
        """Generate 2-3 proactive follow-up questions for the student."""
        prompt = SUGGEST_FOLLOWUP_TEMPLATE.format(question=question, answer=_clip(answer, 1000))
        try:
            raw = self._llm.generate(
                prompt,
                purpose="suggest_followup",
                model=self._utility_model,
                temperature=0.3,
            )
            lines = [line.strip(_STRIP_CHARS) for line in raw.splitlines() if line.strip()]
            return [line for line in lines if line and line.endswith("?")][:3]
        except Exception as exc:
            log.info("rag.suggest_followup_failed", error=str(exc))
            return []

    def _decomposed_queries(self, question: str) -> list[str]:
        """Decompose compound or multi-part questions into sub-queries."""
        try:
            raw = self._llm.generate(
                DECOMPOSE_TEMPLATE.format(question=question),
                purpose="decompose",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:
            log.info("rag.decompose_failed", error=str(exc))
            return []
        lines = [line.strip(_STRIP_CHARS) for line in raw.splitlines() if line.strip()]
        return [line for line in lines if line and line.lower() != question.lower()][:4]

    def _step_back_query(self, question: str) -> str | None:
        """Generate a higher-level, broader query to retrieve background context."""
        try:
            raw = self._llm.generate(
                STEP_BACK_TEMPLATE.format(question=question),
                purpose="step_back",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:
            log.info("rag.step_back_failed", error=str(exc))
            return None
        lines = [line.strip(_STRIP_CHARS) for line in raw.splitlines() if line.strip()]
        if not lines:
            return None
        query = lines[0].strip("\"' ")
        if not query or query.lower() == question.lower():
            return None
        return query[:200]

    def _compress_hit(self, question: str, hit: SearchHit, body: str) -> str:
        """Extract only the sentences and facts directly relevant to the question."""
        if not body.strip():
            return body
        try:
            raw = self._llm.generate(
                COMPRESS_CONTEXT_TEMPLATE.format(question=question, context=body),
                purpose="compress_context",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:
            log.info("rag.compress_failed", error=str(exc))
            return body
        text = raw.strip()
        stripped = text.strip(_STRIP_CHARS)
        if not stripped or stripped == "-":
            return body
        return text

    def _evaluate_relevance(self, question: str, hit: SearchHit) -> float:
        """Score the relevance of a hit to the question on a scale 0.0 to 1.0."""
        try:
            raw = self._llm.generate(
                CRAG_EVALUATE_TEMPLATE.format(question=question, document=hit.text[:1000]),
                purpose="crag_eval",
                model=self._utility_model,
                temperature=0.0,
            )
            # An entity echoed into the response carries twelve hex characters,
            # and the digits among them would be read as the score.
            match = re.search(r"(\d+(?:\.\d+)?)", ENTITY_RE.sub(" ", raw))
            if match:
                score = float(match.group(1))
                return min(max(score, 0.0), 1.0)
        except Exception as exc:
            log.info("rag.crag_eval_failed", error=str(exc))
        return 1.0

    def _crag_filtered(self, question: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Corrective RAG: drop candidates ``_evaluate_relevance`` scores as
        off-topic before they reach reranking/context, rather than trusting fusion
        order alone to keep noise out. One LLM call per candidate.

        Never returns an empty list: if every candidate scores below threshold,
        that is a real "nothing relevant was retrieved" situation the answer
        prompt itself is better placed to refuse from (via REFUSAL_MARKER) than
        an artificially emptied context, which would look like AC-7's "no hits at
        all" case for the wrong reason and skip the LLM call in `answer` entirely.
        """
        kept = [
            h for h in hits if self._evaluate_relevance(question, h) >= self._crag_filter_threshold
        ]
        return kept or hits

    def _actionable_fallback(self, question: str) -> str:
        """Construct an actionable fallback with a direct Moodle search link."""
        from urllib.parse import quote_plus

        base = (self._moodle_base_url or "https://moodle.itech-bs14.de").rstrip("/")
        search_url = f"{base}/search/index.php?q={quote_plus(question)}"
        return (
            f"{NO_ANSWER_TEXT}\n\n"
            f"🔍 **Direktsuche in Moodle:** Du kannst die globale Moodle-Suche ausprobieren:\n"
            f"{search_url}"
        )

    def _expanded_queries(self, question: str) -> list[str]:
        """Bridge casual phrasing to formal course vocabulary (AC-2)."""
        try:
            raw = self._llm.generate(
                EXPAND_TEMPLATE.format(question=question, n=self._expansions),
                purpose="expand",
                model=self._utility_model,
                temperature=0.3,
            )
        except Exception as exc:  # AC-3
            log.info("rag.expand_failed", error=str(exc))
            return []
        variants = [line.strip(_STRIP_CHARS) for line in raw.splitlines() if line.strip()]
        return [v for v in variants if v and v.lower() != question.lower()][: self._expansions]

    def _retrieve(
        self, question: str, queries: list[str], *, room_id: str | None = None
    ) -> list[SearchHit]:
        """Retrieve per query, fuse, boost, and diversify.

        Each query variant is fetched at ``per_query_limit`` — wider than the final
        candidate count (AC-16) — so a document that ranks just outside the cutoff
        for every individual query still has a chance to surface once boosting and
        diversification run over the combined pool.
        """
        ranked_lists: list[list[str]] = []
        by_id: dict[str, SearchHit] = {}
        for query in queries:
            if room_id is not None:
                try:
                    hits = self._searcher.search(
                        query, limit=self._per_query_limit, room_id=room_id
                    )
                except TypeError:
                    hits = self._searcher.search(query, limit=self._per_query_limit)
            else:
                hits = self._searcher.search(query, limit=self._per_query_limit)
            ranked_lists.append([str(h.chunk_id) for h in hits])
            for h in hits:
                by_id.setdefault(str(h.chunk_id), h)

        fused = reciprocal_rank_fusion(ranked_lists)
        scored: list[SearchHit] = []
        for chunk_id, score in fused:
            found = by_id.get(chunk_id)
            if found is not None:
                found.score = score
                scored.append(found)

        boosted = _boost(question, scored, today=self._clock().date())
        diversified = _diversify(boosted, max_per_document=self._max_per_document)
        return diversified[: self._candidates]

    def _reranked(self, question: str, hits: list[SearchHit]) -> tuple[list[SearchHit], int]:
        """Reorder by relevance; also report how many the model was confident in.

        That confidence count (not just the reordered list) is what lets ``answer``
        decide whether a second retrieval hop is worth attempting: a rerank that
        confidently picked only 2 of 12 candidates is a much stronger "this needs
        more" signal than the reordered list alone would expose.
        """
        if self._dated_rerank:
            candidates = "\n\n".join(
                f"[{i}] {h.header_text}{self._stand_suffix(h)}\n{_clip(h.text, 500)}"
                for i, h in enumerate(hits, start=1)
            )
            prompt = RERANK_TEMPLATE_DATED.format(
                question=question,
                today=_format_weekday_date(self._clock()),
                candidates=candidates,
                k=self._max_context,
            )
        else:
            candidates = "\n\n".join(
                f"[{i}] {h.header_text}\n{_clip(h.text, 500)}" for i, h in enumerate(hits, start=1)
            )
            prompt = RERANK_TEMPLATE.format(
                question=question, candidates=candidates, k=self._max_context
            )
        try:
            raw = self._llm.generate(
                prompt,
                purpose="rerank",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:  # AC-4
            log.info("rag.rerank_failed", error=str(exc))
            # Unknown confidence, not low confidence — don't chase a second hop off
            # the back of a rerank failure we already degraded from once.
            return hits, len(hits)

        # Same hazard as `_evaluate_relevance`: a token's hex payload would be
        # scanned as candidate indices.
        order = [int(n) for n in re.findall(r"\d+", ENTITY_RE.sub(" ", raw))]
        chosen: list[SearchHit] = []
        seen: set[int] = set()
        for number in order:
            if 1 <= number <= len(hits) and number not in seen:
                seen.add(number)
                chosen.append(hits[number - 1])
        if not chosen:
            return hits, 0
        confident = len(chosen)
        # Keep unselected hits as a tail so a bad rerank cannot starve the context.
        chosen.extend(h for i, h in enumerate(hits, start=1) if i not in seen)
        return chosen, confident

    def _followup_query(self, question: str, hits: list[SearchHit]) -> str | None:
        """Ask whether the current context references something worth chasing.

        Single call, question-answerable-with-one-search style: the model looks at
        what was actually retrieved and names one concrete follow-up search term —
        classroom vocabulary the original question didn't use — or says there is
        nothing more to look for.
        """
        excerpts = "\n\n".join(
            f"[{i}] {h.header_text}\n{_clip(h.text, 500)}" for i, h in enumerate(hits, start=1)
        )
        try:
            raw = self._llm.generate(
                FOLLOWUP_TEMPLATE.format(question=question, excerpts=excerpts),
                purpose="followup",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:
            log.info("rag.followup_failed", error=str(exc))
            return None
        query = raw.strip().splitlines()[0].strip(_STRIP_CHARS) if raw.strip() else ""
        if not query or query.lower() == question.lower():
            return None
        return query[:200]

    def _expanded_body(self, hit: SearchHit) -> str:
        """Splice a hit's chunk with its ``neighbor_radius`` neighbours in the same
        document (see ``DEFAULT_NEIGHBOR_RADIUS``); falls back to the hit's own
        content alone if it has no neighbours (or isn't part of a multi-chunk
        document at all).
        """
        neighbors = self._searcher.neighbors(hit.chunk_id, radius=self._neighbor_radius)
        if not neighbors:
            return hit.body or hit.text
        return "\n\n".join((n.body or n.text) for n in neighbors)

    def _stand_suffix(self, hit: SearchHit) -> str:
        """ " (Stand: TT.MM.JJJJ)" for a hit with a known source date, else ""."""
        if not hit.source_date:
            return ""
        source_date = _format_weekday_date(
            datetime.fromtimestamp(hit.source_date, tz=_SCHOOL_TZ), weekday=False
        )
        return f" (Stand: {source_date})"

    def _format_context(self, hits: list[SearchHit], question: str | None = None) -> str:
        blocks = []
        for index, hit in enumerate(hits, start=1):
            location = f", S. {hit.page}" if hit.page else ""
            stand = self._stand_suffix(hit)
            body = self._expanded_body(hit)
            if self._compress_context and question:
                body = self._compress_hit(question, hit, body)
            blocks.append(
                f"[QUELLE {index}] {hit.course_name} – {hit.header_text}{location}{stand}\n"
                f"{_clip(body, MAX_EXPANDED_CHUNK_CHARS)}"
            )
        return "\n\n".join(blocks)

    def _finalise(
        self,
        raw: str,
        hits: list[SearchHit],
        queries: list[str],
        question: str = "",
        raw_question: str = "",
    ) -> Answer:
        text = raw.strip()
        doc_ids = [h.doc_id for h in hits]
        if REFUSAL_MARKER in text:  # AC-10
            # A local URL, never sent to Gemini — must reflect the student's
            # literal wording, not the tokenized search query.
            fallback_question = raw_question or question
            fallback_text = (
                self._actionable_fallback(fallback_question)
                if (self._crag and fallback_question)
                else NO_ANSWER_TEXT
            )
            return Answer(
                text=fallback_text, grounded=False, used_queries=queries, context_doc_ids=doc_ids
            )

        # PII tokenization (spec 013): everything upstream of here worked in
        # "token space" — resolve tokens back to real values before the answer
        # leaves the pipeline. This is the single point that does so.
        text = self._detok(text)

        # AC-9: only keep citation indexes that were actually offered.
        # The model groups citations as "[1, 2]" at least as often as "[1] [2]",
        # so parse the whole bracket rather than a single number.
        cited = sorted(
            {
                int(number)
                for group in re.findall(r"\[([\d,\s]+)\]", text)
                for number in re.findall(r"\d+", group)
            }
        )
        citations = [
            Citation(
                index=index,
                title=self._detok(hits[index - 1].title),
                course_name=self._detok(hits[index - 1].course_name),
                header_text=self._detok(hits[index - 1].header_text),
                url=hits[index - 1].module_url,
                page=hits[index - 1].page,
            )
            for index in cited
            if 1 <= index <= len(hits)
        ]
        return Answer(
            text=text,
            citations=citations,
            grounded=True,
            used_queries=queries,
            context_doc_ids=doc_ids,
        )


#: "LF10", "LF 10", "Lernfeld10", "lf06" — every phrasing a student actually types.
_LERNFELD_RE = re.compile(r"\b(?:lf|lernfeld)\s*0?(\d{1,2})\b", re.I)

_WEEKDAYS_DE_LOWER = (
    "montag",
    "dienstag",
    "mittwoch",
    "donnerstag",
    "freitag",
    "samstag",
    "sonntag",
)


def _detect_named_lernfeld(question: str) -> str | None:
    """Pull an explicitly-named Lernfeld number out of the question, if any."""
    match = _LERNFELD_RE.search(question)
    return match.group(1) if match else None


def _lernfeld_needles(number: str) -> tuple[str, ...]:
    return (
        f"lernfeld {number}",
        f"lernfeld 0{number}",
        f"lernfeld{number}",
        f"lernfeld0{number}",
        f"lf{number}",
        f"lf0{number}",
        f"lf {number}",
        f"lf 0{number}",
    )


def _detect_target_date(question: str, today: date) -> date | None:
    """Resolve a relative date reference ('morgen', 'am Montag'...) to a real date.

    A schedule document (a Blockplan) is chunked across many chunks, one per week,
    each containing a different, mutually exclusive set of dates — BM25/embedding
    scores cannot tell "the chunk for next week" from "the chunk for six weeks
    ago", since both are about a Blockplan equally. Resolving the question's own
    date reference against the school's real clock is what makes that distinction
    checkable in _boost below.
    """
    q = question.lower()
    if "übermorgen" in q:
        return today + timedelta(days=2)
    if "morgen" in q:
        return today + timedelta(days=1)
    if re.search(r"\bheute\b", q):
        return today
    for index, name in enumerate(_WEEKDAYS_DE_LOWER):
        if name in q:
            return today + timedelta(days=(index - today.weekday()) % 7)
    return None


def _boost(question: str, hits: list[SearchHit], *, today: date) -> list[SearchHit]:
    """Rank candidates matching an explicit signal in the question first (AC-18
    for Lernfeld; the date case is the same idea applied to schedule documents).

    Both signals are exact, low-noise, and already present in the source text —
    the crawler's breadcrumb already says "Lernfeld 10", a Blockplan chunk already
    says "2026-08-24" — a generic retriever has no way to know either one matters
    more than topical similarity, so whichever the question actually names is
    honoured over whatever BM25/embedding scores happened to produce.
    """
    lernfeld = _detect_named_lernfeld(question)
    lernfeld_needles = _lernfeld_needles(lernfeld) if lernfeld else ()
    target_date = _detect_target_date(question, today)
    date_needles = (f"{target_date:%Y-%m-%d}", f"{target_date:%d.%m.%Y}") if target_date else ()

    def sort_key(hit: SearchHit) -> tuple[bool, bool, float]:
        date_match = any(needle in hit.text for needle in date_needles)
        lernfeld_match = any(needle in hit.header_text.lower() for needle in lernfeld_needles)
        return (date_match, lernfeld_match, hit.score)

    return sorted(hits, key=sort_key, reverse=True)


def _diversify(hits: list[SearchHit], *, max_per_document: int) -> list[SearchHit]:
    """Cap how many chunks from one document occupy the ranked list (AC-17).

    Applied after sorting: within a document's cap, its highest-ranked chunks are
    kept and the rest skipped, so a handful of near-duplicate chunks from one file
    cannot crowd out a different, relevant document.
    """
    counts: dict[str, int] = {}
    result: list[SearchHit] = []
    for hit in hits:
        seen = counts.get(hit.doc_id, 0)
        if seen >= max_per_document:
            continue
        counts[hit.doc_id] = seen + 1
        result.append(hit)
    return result


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _format_weekday_date(when: datetime, *, weekday: bool = True) -> str:
    """German ``Donnerstag, 21.08.2026`` — never locale-dependent ``%A``.

    A Docker image typically has no German locale installed, so ``strftime("%A")``
    would silently render English weekday names; spelling this out avoids that.
    """
    date = f"{when:%d.%m.%Y}"
    return f"{_WEEKDAYS_DE[when.weekday()]}, {date}" if weekday else date


__all__ = ["NO_ANSWER_TEXT", "REFUSAL_MARKER", "Answer", "AnswerPipeline", "Citation"]
