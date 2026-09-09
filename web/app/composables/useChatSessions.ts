import { useLocalStorage } from '@vueuse/core'
import { useIDBKeyval } from '@vueuse/integrations/useIDBKeyval'

const MAX_HISTORY_TURNS = 10
const TITLE_MAX_LENGTH = 40

function createEmptySession(): ChatSession {
  const now = Date.now()
  return {
    id: crypto.randomUUID(),
    title: '',
    titleIsManual: false,
    messages: [],
    history: [],
    createdAt: now,
    updatedAt: now
  }
}

function deriveTitle(question: string): string {
  return question.length > TITLE_MAX_LENGTH ? `${question.slice(0, TITLE_MAX_LENGTH)}…` : question
}

function byMostRecent(a: ChatSession, b: ChatSession): number {
  return b.updatedAt - a.updatedAt
}

// Deep-unwraps Vue's reactive proxies into plain data — IndexedDB's
// structured-clone step rejects Proxies outright (DataCloneError), even
// though they read/serialize fine everywhere else. Safe here because
// ChatSession is plain JSON-shaped data (no Date/Map/etc).
function toPlain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

export function useChatSessions() {
  // useIDBKeyval calls idb-keyval's get() unconditionally, which throws
  // "indexedDB is not defined" under SSR — this branch is compiled away per
  // target (import.meta.client/server are build-time constants), so the
  // server bundle never reaches the real call.
  const { sessions, sessionsLoaded } = import.meta.client
    ? (() => {
        const { data, isFinished } = useIDBKeyval<ChatSession[]>('bsbot-chat-sessions', [])
        return { sessions: data, sessionsLoaded: isFinished }
      })()
    : { sessions: ref<ChatSession[]>([]), sessionsLoaded: ref(false) }

  const activeSessionId = useLocalStorage<string | null>('bsbot-chat-active-session-id', null)

  const ready = computed(() => sessionsLoaded.value)
  const sortedSessions = computed(() => [...sessions.value].sort(byMostRecent))
  const activeSession = computed(() => sessions.value.find(s => s.id === activeSessionId.value) ?? null)

  function updateSession(id: string, mutate: (session: ChatSession) => ChatSession) {
    sessions.value = toPlain(sessions.value.map(s => (s.id === id ? mutate(s) : s)))
  }

  function createSession(): ChatSession {
    const session = createEmptySession()
    sessions.value = toPlain([...sessions.value, session])
    activeSessionId.value = session.id
    return session
  }

  function switchSession(id: string) {
    if (sessions.value.some(s => s.id === id)) {
      activeSessionId.value = id
    }
  }

  function renameSession(id: string, title: string) {
    const trimmed = title.trim()
    if (!trimmed) return
    updateSession(id, s => ({ ...s, title: trimmed, titleIsManual: true, updatedAt: Date.now() }))
  }

  function deleteSession(id: string) {
    sessions.value = toPlain(sessions.value.filter(s => s.id !== id))
    if (activeSessionId.value === id) {
      activeSessionId.value = sortedSessions.value[0]?.id ?? createSession().id
    }
  }

  // First-ever visit, or the active session got removed (e.g. deleted from
  // another tab — sessions sync across tabs via useIDBKeyval's
  // BroadcastChannel) — always leave something to point at once loaded.
  watchEffect(() => {
    if (!ready.value || activeSession.value) return
    activeSessionId.value = sortedSessions.value[0]?.id ?? createSession().id
  })

  const input = ref('')
  const status = ref<'ready' | 'submitted' | 'streaming' | 'error'>('ready')

  function isLatest(message: ChatMessage): boolean {
    return activeSession.value?.messages.at(-1)?.id === message.id
  }

  async function ask(rawQuestion: string): Promise<void> {
    const question = rawQuestion.trim()
    const session = activeSession.value
    if (!question || status.value === 'submitted' || !session) return

    const isFirstMessage = session.messages.length === 0

    // New array/object references throughout, not in-place mutation:
    // UChatMessages (via activeSession.messages) watches for reference
    // changes rather than deep mutations.
    updateSession(session.id, s => ({
      ...s,
      title: !s.titleIsManual && isFirstMessage ? deriveTitle(question) : s.title,
      messages: [...s.messages, { id: crypto.randomUUID(), role: 'user', parts: [{ type: 'text', text: question }] }],
      updatedAt: Date.now()
    }))
    input.value = ''
    status.value = 'submitted'

    try {
      const answer = await $fetch<AskResponse>('/api/chat', {
        method: 'POST',
        body: { question, history: session.history }
      })
      updateSession(session.id, s => ({
        ...s,
        messages: [...s.messages, {
          id: crypto.randomUUID(),
          role: 'assistant',
          parts: [{ type: 'text', text: answer.text }],
          citations: answer.citations,
          suggestedQuestions: answer.suggested_questions
        }],
        history: answer.grounded ? [...s.history, [question, answer.text] as [string, string]].slice(-MAX_HISTORY_TURNS) : s.history,
        updatedAt: Date.now()
      }))
    } catch (error) {
      // the question never got an answer
      updateSession(session.id, s => ({ ...s, messages: s.messages.slice(0, -1) }))
      throw error
    } finally {
      status.value = 'ready'
    }
  }

  return {
    sessions: sortedSessions,
    activeSessionId,
    activeSession,
    ready,
    createSession,
    switchSession,
    renameSession,
    deleteSession,
    input,
    status,
    isLatest,
    ask
  }
}
