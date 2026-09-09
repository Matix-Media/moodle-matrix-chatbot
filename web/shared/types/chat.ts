export interface Citation {
  index: number
  header_text: string
  url: string | null
  page: number | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  parts: Array<{ type: 'text', text: string }>
  citations?: Citation[]
  suggestedQuestions?: string[]
}

export interface AskResponse {
  text: string
  citations: Citation[]
  grounded: boolean
  suggested_questions: string[]
}

export interface ChatSession {
  id: string
  // '' until the first user message derives one, or the user renames it.
  title: string
  // Once true, ask() stops overwriting title with an auto-derived one.
  titleIsManual: boolean
  messages: ChatMessage[]
  // Grounded turns only, capped to the last 10 — resent on every request so
  // pipeline.answer() can condense follow-ups against prior context.
  history: Array<[string, string]>
  createdAt: number
  updatedAt: number
}
