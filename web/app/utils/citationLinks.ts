// Matches the model's inline citation markers, e.g. "[1]" or "[1, 2]" — kept
// in sync with the extraction regex in src/bsbot/rag/pipeline.py::_finalise.
const CITATION_GROUP_RE = /\[([\d,\s]+)\]/g

// One citation's data as carried inside a cite:// link's `c` query param —
// everything ChatCitationLink.global.vue needs to render the chip and hover
// card, so it never needs access to the surrounding app state.
export interface CitationLinkEntry {
  u: string | null
  t: string
  l: string
  i: string
}

/**
 * Rewrite inline "[1]" / "[1, 2]" citation markers into a single MDC-parseable
 * link per bracket group (Google-style: one chip per cited statement, not one
 * per number) — see ChatCitationLink.global.vue for how the result is
 * rendered, and web/app/pages/index.vue for how this feeds into <MDC>.
 *
 * A bracketed group that doesn't resolve to any known citation is left as
 * plain text (it wasn't in message.citations to begin with, e.g. the model
 * cited an index the pipeline already dropped per AC-9).
 */
export function citationsToLinks(text: string, citations: Citation[]): string {
  if (citations.length === 0) return text
  const byIndex = new Map(citations.map(c => [c.index, c]))
  return text.replace(CITATION_GROUP_RE, (match, group: string) => {
    const numbers = group.match(/\d+/g)
    if (!numbers) return match
    const matched = numbers
      .map(n => byIndex.get(Number(n)))
      .filter((c): c is Citation => c !== undefined)
    return matched.length ? citationGroupLink(matched) : match
  })
}

function citationGroupLink(group: Citation[]): string {
  const entries: CitationLinkEntry[] = group.map(c => ({
    u: c.url,
    t: c.header_text,
    l: c.course_name,
    i: citationSource(c.url).icon
  }))
  const encoded = encodeURIComponent(JSON.stringify(entries))
  // The link text itself is never shown — ChatCitationLink renders its own
  // badge from the `c` payload — so it's just a placeholder for valid markdown.
  return `[·](cite://ref?c=${encoded})`
}
