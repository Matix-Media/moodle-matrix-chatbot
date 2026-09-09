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
