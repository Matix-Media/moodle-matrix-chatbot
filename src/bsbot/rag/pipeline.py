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
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from bsbot.index.search import SearchHit, reciprocal_rank_fusion
from bsbot.rag.prompts import (
    ANSWER_TEMPLATE,
    EXPAND_TEMPLATE,
    FOLLOWUP_TEMPLATE,
    REFUSAL_MARKER,
    RERANK_TEMPLATE,
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
#: 5 chunks (radius 2) should give the model more to work with, not let one
#: unusually verbose document balloon the whole prompt.
MAX_EXPANDED_CHUNK_CHARS = MAX_CHUNK_CHARS * 3

#: Chars stripped from a model line before treating it as real content. Includes
#: Unicode dash variants deliberately, not just ASCII hyphen-minus: asked for "einen
#: Bindestrich", Gemini returns an en-dash (–, U+2013) far more often than "-"
#: (U+002D) — observed live: 4 of 5 real calls. Stripping only "-" left "–" looking
#: like a genuine (garbage) search query instead of "nothing to add".
_STRIP_CHARS = " -–—•\t"

#: The audience is one Hamburg Berufsschule, so its own timezone, not server-local
#: or UTC time, decides what "heute"/"morgen" mean. tzdata is a pinned dependency
#: (see pyproject.toml) so this resolves correctly even on a minimal Docker image
#: that has no system timezone database.
_SCHOOL_TZ = ZoneInfo("Europe/Berlin")
_WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

NO_ANSWER_TEXT = (
    "Dazu finde ich nichts in Moodle. Vielleicht steht es in einem Kurs, auf den ich "
    "keinen Zugriff habe – oder frag bitte direkt bei der Lehrkraft nach."
)


class SearcherLike(Protocol):
    def search(self, query: str, *, limit: int = 8) -> list[SearchHit]: ...
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


class AnswerPipeline:
    def __init__(
        self,
        searcher: SearcherLike,
        llm: LLMLike,
        *,
        expand: bool = True,
        rerank: bool = True,
        followup: bool = True,
        max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
        candidates: int = DEFAULT_CANDIDATES,
        expansions: int = DEFAULT_EXPANSIONS,
        per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
        max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
        neighbor_radius: int = DEFAULT_NEIGHBOR_RADIUS,
        utility_model: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(_SCHOOL_TZ),
    ) -> None:
        self._searcher = searcher
        self._llm = llm
        self._expand = expand
        self._rerank = rerank
        self._followup = followup
        self._max_context = max_context_chunks
        self._candidates = candidates
        self._expansions = expansions
        self._per_query_limit = max(per_query_limit, candidates)
        self._neighbor_radius = neighbor_radius
        self._max_per_document = max_per_document
        self._utility_model = utility_model
        self._clock = clock

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def answer(self, question: str) -> Answer:
        question = (question or "").strip()
        log.info("rag.question", question=question)
        queries = [question] if question else []
        if question and self._expand:
            queries.extend(self._expanded_queries(question))

        hits = self._retrieve(question, queries)
        log.info(
            "rag.retrieved",
            queries=queries,
            hits=len(hits),
            headers=[h.header_text for h in hits[: self._max_context]],
        )
        if not hits:
            result = Answer(text=NO_ANSWER_TEXT, grounded=False, used_queries=queries)
            log.info("rag.answered", grounded=False, citations=0)
            return result

        if self._rerank:
            hits, confident = self._reranked(question, hits)
            # A second, bounded hop: whenever the first pass looks thin — the
            # reranker was confident in fewer chunks than we want for context, the
            # classic shape of a question whose real terminology (a TaskCards term,
            # an abbreviation) never appeared in the original question or its
            # expansions. This runs a genuinely new query, not a re-search of the
            # same pool, so a small original hit count is not a reason to skip it —
            # if anything it is a stronger signal something is missing. One hop
            # only — the result of this second pass is never re-assessed.
            if self._followup and confident < self._max_context:
                followup_query = self._followup_query(question, hits[: self._max_context])
                if followup_query:
                    queries = [*queries, followup_query]
                    retried = self._retrieve(question, queries)
                    log.info("rag.followup", query=followup_query, hits=len(retried))
                    if retried:
                        hits, confident = self._reranked(question, retried)
        context_hits = hits[: self._max_context]

        prompt = ANSWER_TEMPLATE.format(
            today=_format_weekday_date(self._clock()),
            question=question,
            context=self._format_context(context_hits),
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
            )
            log.info("rag.answered", grounded=False, citations=0)
            return result

        result = self._finalise(raw, context_hits, queries)
        log.info("rag.answered", grounded=result.grounded, citations=len(result.citations))
        return result

    # ------------------------------------------------------------------ #

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

    def _retrieve(self, question: str, queries: list[str]) -> list[SearchHit]:
        """Retrieve per query, fuse, boost, and diversify.

        Each query variant is fetched at ``per_query_limit`` — wider than the final
        candidate count (AC-16) — so a document that ranks just outside the cutoff
        for every individual query still has a chance to surface once boosting and
        diversification run over the combined pool.
        """
        ranked_lists: list[list[str]] = []
        by_id: dict[str, SearchHit] = {}
        for query in queries:
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
        candidates = "\n\n".join(
            f"[{i}] {h.header_text}\n{_clip(h.text, 500)}" for i, h in enumerate(hits, start=1)
        )
        try:
            raw = self._llm.generate(
                RERANK_TEMPLATE.format(
                    question=question, candidates=candidates, k=self._max_context
                ),
                purpose="rerank",
                model=self._utility_model,
                temperature=0.0,
            )
        except Exception as exc:  # AC-4
            log.info("rag.rerank_failed", error=str(exc))
            # Unknown confidence, not low confidence — don't chase a second hop off
            # the back of a rerank failure we already degraded from once.
            return hits, len(hits)

        order = [int(n) for n in re.findall(r"\d+", raw)]
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

    def _format_context(self, hits: list[SearchHit]) -> str:
        blocks = []
        for index, hit in enumerate(hits, start=1):
            location = f", S. {hit.page}" if hit.page else ""
            source_date = (
                _format_weekday_date(
                    datetime.fromtimestamp(hit.source_date, tz=_SCHOOL_TZ), weekday=False
                )
                if hit.source_date
                else None
            )
            stand = f" (Stand: {source_date})" if source_date else ""
            body = self._expanded_body(hit)
            blocks.append(
                f"[QUELLE {index}] {hit.course_name} – {hit.header_text}{location}{stand}\n"
                f"{_clip(body, MAX_EXPANDED_CHUNK_CHARS)}"
            )
        return "\n\n".join(blocks)

    def _finalise(self, raw: str, hits: list[SearchHit], queries: list[str]) -> Answer:
        text = raw.strip()
        if REFUSAL_MARKER in text:  # AC-10
            return Answer(text=NO_ANSWER_TEXT, grounded=False, used_queries=queries)

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
                title=hits[index - 1].title,
                course_name=hits[index - 1].course_name,
                header_text=hits[index - 1].header_text,
                url=hits[index - 1].module_url,
                page=hits[index - 1].page,
            )
            for index in cited
            if 1 <= index <= len(hits)
        ]
        return Answer(text=text, citations=citations, grounded=True, used_queries=queries)


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
