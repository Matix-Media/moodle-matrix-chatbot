<script setup lang="ts">
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

const {
  sessions,
  activeSessionId,
  activeSession,
  createSession,
  switchSession,
  renameSession,
  deleteSession,
  input,
  status,
  isLatest,
  ask: askSession
} = useChatSessions()

const sidebarOpen = ref(false)

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

async function ask(rawQuestion: string) {
  try {
    await askSession(rawQuestion)
  } catch (error) {
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
  }
}

function onSubmit() {
  ask(input.value)
}

function onCreateSession() {
  createSession()
  sidebarOpen.value = false
}

function onSwitchSession(id: string) {
  switchSession(id)
  sidebarOpen.value = false
}
</script>

<template>
  <div class="h-dvh flex bg-default overflow-hidden">
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-30 bg-black/50 md:hidden"
      @click="sidebarOpen = false"
    />

    <aside
      class="fixed inset-y-0 left-0 z-40 w-72 border-r border-default bg-default transition-transform md:static md:z-auto md:w-64 md:shrink-0 md:translate-x-0"
      :class="sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <ChatSessionSidebar
        :sessions="sessions"
        :active-session-id="activeSessionId"
        @create="onCreateSession"
        @switch="onSwitchSession"
        @rename="renameSession"
        @delete="deleteSession"
      />
    </aside>

    <div class="flex-1 min-w-0 flex flex-col">
      <header class="shrink-0 border-b border-default px-4 py-3 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <UButton
            icon="i-lucide-menu"
            variant="ghost"
            color="neutral"
            class="md:hidden"
            @click="sidebarOpen = true"
          />
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

      <div class="flex-1 min-h-0 flex flex-col">
        <div class="flex-1 min-h-0 overflow-y-auto">
          <UContainer class="w-full max-w-3xl h-full flex flex-col py-4">
            <div
              v-if="!activeSession || activeSession.messages.length === 0"
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
              :messages="activeSession.messages"
              :status="status"
              should-auto-scroll
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
                    <MDC :value="citationsToLinks(part.text, message.citations ?? [])" />
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
                    :icon="citationSource(citation.url).icon"
                  >
                    [{{ citation.index }}] {{ citation.header_text }}
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
                    class="justify-start text-left"
                    @click="ask(q)"
                  >
                    {{ q }}
                  </UButton>
                </div>
              </template>
            </UChatMessages>
          </UContainer>
        </div>

        <UContainer class="w-full max-w-3xl shrink-0 flex flex-col gap-2 pb-4">
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
      </div>
    </div>

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
