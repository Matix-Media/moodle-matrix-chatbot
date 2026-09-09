<script setup lang="ts">
interface Citation {
  index: number
  header_text: string
  url: string | null
  page: number | null
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  parts: Array<{ type: 'text', text: string }>
  citations?: Citation[]
  suggestedQuestions?: string[]
}

interface AskResponse {
  text: string
  citations: Citation[]
  grounded: boolean
  suggested_questions: string[]
}

const EXAMPLE_QUESTIONS = [
  'Wie melde ich mich krank?',
  'Wann ist die nächste Prüfung?',
  'Wo finde ich den Stundenplan?'
]

const route = useRoute()
const router = useRouter()
const toast = useToast()

// Not `secure: true` — this app is meant to sit behind a reverse proxy that
// may terminate TLS, but also needs to work over plain http for local
// `docker compose up` testing (see specs/014-web-chat.md's verification steps).
const tokenCookie = useCookie<string | null>('bs_chat_token', {
  default: () => null,
  sameSite: 'lax',
  maxAge: 60 * 60 * 24 * 365
})

const showTokenModal = ref(false)
const tokenInput = ref('')
const tokenError = ref<string | null>(null)

const messages = ref<ChatMessage[]>([])
// Grounded turns only, sent back on every request so pipeline.answer() can
// condense follow-ups against prior context — capped to bound request size,
// matching BotPolicy.max_matrix_history's existing default of 10.
const history = ref<Array<[string, string]>>([])
const input = ref('')
const status = ref<'ready' | 'submitted' | 'streaming' | 'error'>('ready')

onMounted(() => {
  const queryToken = route.query.token
  if (typeof queryToken === 'string' && queryToken) {
    tokenCookie.value = queryToken
    const { token: _token, ...rest } = route.query
    router.replace({ query: rest })
  }
  if (!tokenCookie.value) {
    showTokenModal.value = true
  }
})

function submitToken() {
  const value = tokenInput.value.trim()
  if (!value) return
  tokenCookie.value = value
  tokenInput.value = ''
  tokenError.value = null
  showTokenModal.value = false
}

function isLatest(message: ChatMessage): boolean {
  return messages.value.at(-1)?.id === message.id
}

async function ask(rawQuestion: string) {
  const question = rawQuestion.trim()
  if (!question || status.value === 'submitted') return

  // New array references, not in-place mutation: UChatMessages watches for
  // reference changes rather than deep array mutations, so `.push()` here
  // silently fails to re-render (found while testing this against a live
  // backend — the request succeeded but the reply never appeared).
  messages.value = [...messages.value, { id: crypto.randomUUID(), role: 'user', parts: [{ type: 'text', text: question }] }]
  input.value = ''
  status.value = 'submitted'

  try {
    const answer = await $fetch<AskResponse>('/api/chat', {
      method: 'POST',
      body: { question, history: history.value }
    })
    messages.value = [...messages.value, {
      id: crypto.randomUUID(),
      role: 'assistant',
      parts: [{ type: 'text', text: answer.text }],
      citations: answer.citations,
      suggestedQuestions: answer.suggested_questions
    }]
    if (answer.grounded) {
      history.value = [...history.value, [question, answer.text] as [string, string]].slice(-10)
    }
  } catch (error) {
    messages.value = messages.value.slice(0, -1) // the question never got an answer

    const statusCode = (error as { statusCode?: number })?.statusCode
    if (statusCode === 401) {
      tokenCookie.value = null
      tokenError.value = 'Der Zugangs-Code ist ungültig oder abgelaufen.'
      showTokenModal.value = true
    } else if (statusCode === 429) {
      toast.add({ title: 'Zu viele Anfragen', description: 'Bitte versuch es gleich nochmal.', color: 'warning' })
    } else {
      toast.add({ title: 'Fehler', description: 'Da ist etwas schiefgelaufen. Bitte versuch es nochmal.', color: 'error' })
    }
  } finally {
    // Not a persistent 'error' status: the toast/modal above already told
    // the user what went wrong, and UChatPromptSubmit renders a dead
    // (type="button", non-submitting) retry icon while status is 'error' —
    // found live: after a failed request the send button silently stopped
    // submitting anything at all. Always settle back to 'ready'.
    status.value = 'ready'
  }
}

function onSubmit() {
  ask(input.value)
}
</script>

<template>
  <div class="h-screen flex flex-col bg-default">
    <header class="shrink-0 border-b border-default px-4 py-3 flex items-center justify-between">
      <div class="flex items-center gap-2.5">
        <UIcon
          name="i-lucide-graduation-cap"
          class="size-6 text-primary"
        />
        <div class="leading-tight">
          <h1 class="font-semibold text-highlighted">
            Moodle-Chat
          </h1>
          <p class="text-xs text-muted">
            Frag mich zu deinem Kurs
          </p>
        </div>
      </div>
      <UColorModeButton />
    </header>

    <UContainer class="flex-1 min-h-0 flex flex-col w-full max-w-3xl py-4 gap-2">
      <div
        v-if="messages.length === 0"
        class="flex-1 flex flex-col items-center justify-center gap-4 text-center px-4"
      >
        <UIcon
          name="i-lucide-message-circle"
          class="size-10 text-muted"
        />
        <div>
          <p class="font-medium text-highlighted">
            Womit kann ich helfen?
          </p>
          <p class="text-sm text-muted mt-1">
            Stell eine Frage zu deinem Moodle-Kurs.
          </p>
        </div>
        <div class="flex flex-wrap justify-center gap-2">
          <UButton
            v-for="q in EXAMPLE_QUESTIONS"
            :key="q"
            variant="outline"
            color="neutral"
            size="sm"
            @click="ask(q)"
          >
            {{ q }}
          </UButton>
        </div>
      </div>

      <UChatMessages
        v-else
        :messages="messages"
        :status="status"
        should-auto-scroll
        class="flex-1 min-h-0 overflow-y-auto"
        :user="{ side: 'right', variant: 'solid', avatar: { icon: 'i-lucide-user' }, ui: { leading: 'order-last' } }"
        :assistant="{ side: 'left', variant: 'soft', avatar: { icon: 'i-lucide-graduation-cap' } }"
      >
        <template #content="{ message }">
          <template
            v-for="(part, i) in message.parts"
            :key="i"
          >
            <div
              v-if="message.role === 'assistant'"
              class="chat-markdown"
            >
              <MDC :value="part.text" />
            </div>
            <p
              v-else
              class="whitespace-pre-wrap"
            >
              {{ part.text }}
            </p>
          </template>

          <div
            v-if="message.citations?.length"
            class="mt-2 flex flex-wrap gap-1.5"
          >
            <UButton
              v-for="citation in message.citations"
              :key="citation.index"
              :to="citation.url ?? undefined"
              :disabled="!citation.url"
              target="_blank"
              variant="soft"
              color="neutral"
              size="xs"
              icon="i-lucide-external-link"
            >
              {{ citation.header_text }}
            </UButton>
          </div>

          <div
            v-if="isLatest(message) && message.suggestedQuestions?.length"
            class="mt-3 flex flex-wrap gap-1.5"
          >
            <UButton
              v-for="q in message.suggestedQuestions"
              :key="q"
              variant="outline"
              color="primary"
              size="xs"
              @click="ask(q)"
            >
              {{ q }}
            </UButton>
          </div>
        </template>
      </UChatMessages>

      <UChatShimmer
        v-if="status === 'submitted'"
        text="Antwort wird generiert…"
        class="px-1"
      />

      <UChatPrompt
        v-model="input"
        placeholder="Stell eine Frage zu deinem Kurs…"
        variant="subtle"
        :disabled="status === 'submitted'"
        @submit="onSubmit"
      >
        <template #footer>
          <UChatPromptSubmit
            :status="status"
            class="ms-auto"
          />
        </template>
      </UChatPrompt>
    </UContainer>

    <UModal
      v-model:open="showTokenModal"
      title="Zugang erforderlich"
      :dismissible="false"
      :close="false"
    >
      <template #body>
        <form
          class="flex flex-col gap-3"
          @submit.prevent="submitToken"
        >
          <div class="flex items-center gap-2 text-muted">
            <UIcon
              name="i-lucide-lock"
              class="size-5 shrink-0"
            />
            <p class="text-sm">
              Gib den Zugangs-Code für den Chat ein.
            </p>
          </div>
          <UInput
            v-model="tokenInput"
            type="password"
            placeholder="Zugangs-Code"
            icon="i-lucide-key-round"
            autofocus
          />
          <p
            v-if="tokenError"
            class="text-sm text-error"
          >
            {{ tokenError }}
          </p>
          <UButton
            type="submit"
            block
            :disabled="!tokenInput.trim()"
          >
            Bestätigen
          </UButton>
        </form>
      </template>
    </UModal>
  </div>
</template>

<style scoped>
.chat-markdown :deep(p) {
  margin: 0 0 0.5em;
}
.chat-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.chat-markdown :deep(strong) {
  font-weight: 600;
  color: var(--ui-text-highlighted);
}
.chat-markdown :deep(a) {
  color: var(--ui-primary);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.chat-markdown :deep(a:hover) {
  opacity: 0.8;
}
.chat-markdown :deep(ul),
.chat-markdown :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.25em;
}
.chat-markdown :deep(ul) {
  list-style: disc;
}
.chat-markdown :deep(ol) {
  list-style: decimal;
}
.chat-markdown :deep(li) {
  margin: 0.15em 0;
}
.chat-markdown :deep(code) {
  background: var(--ui-bg-elevated);
  border-radius: 0.25rem;
  padding: 0.1em 0.35em;
  font-size: 0.85em;
}
</style>
