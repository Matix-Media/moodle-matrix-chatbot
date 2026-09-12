export interface CitationSource {
  icon: string
  label: string
}

const MOODLE_SOURCE: CitationSource = { icon: 'i-simple-icons-moodle', label: 'Moodle' }
const MATRIX_SOURCE: CitationSource = { icon: 'i-simple-icons-matrix', label: 'Matrix' }

/**
 * Every citation url is either a Moodle module link or a `matrix.to/#/...`
 * link produced by `store.index_matrix_message` (see src/bsbot/index/store.py)
 * — there is no third case today, so anything unrecognised (including a null
 * url) falls back to Moodle rather than growing a generic "unknown" badge.
 */
export function citationSource(url: string | null): CitationSource {
  if (!url) return MOODLE_SOURCE
  try {
    const hostname = new URL(url).hostname.toLowerCase()
    if (hostname === 'matrix.to' || hostname.endsWith('.matrix.to')) return MATRIX_SOURCE
  } catch {
    // fall through to the Moodle default
  }
  return MOODLE_SOURCE
}

/**
 * Collapse citations that resolve to the same underlying document. The
 * retrieval pipeline can surface more than one chunk of the same page as
 * separate hits (see `AnswerPipeline._finalise` / `max_per_document` in
 * src/bsbot/rag/pipeline.py), so the model can legitimately cite two
 * different indices that both point at one source — without this, that
 * source renders twice (once per index) in the sources list and in the
 * inline hover card.
 *
 * Keyed by url when present (the reliable identity for a document); falls
 * back to title for the rare linkless citation, since two null-url
 * citations sharing a title are still the same source as far as the UI can
 * tell.
 */
export function dedupeCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>()
  return citations.filter((c) => {
    const key = c.url ?? `title:${c.title}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
