<script setup lang="ts">
const props = defineProps<{
  sessions: ChatSession[]
  activeSessionId: string | null
}>()

const emit = defineEmits<{
  create: []
  switch: [id: string]
  rename: [id: string, title: string]
  delete: [id: string]
}>()

const editingId = ref<string | null>(null)
const editingTitle = ref('')
const deleteTarget = ref<ChatSession | null>(null)

function sessionLabel(session: ChatSession): string {
  return session.title || 'Neuer Chat'
}

function startRename(session: ChatSession) {
  editingId.value = session.id
  editingTitle.value = sessionLabel(session)
}

function confirmRename() {
  if (editingId.value) {
    emit('rename', editingId.value, editingTitle.value)
  }
  editingId.value = null
}

function cancelRename() {
  editingId.value = null
}

function requestDelete(session: ChatSession) {
  deleteTarget.value = session
}

function confirmDelete() {
  if (deleteTarget.value) {
    emit('delete', deleteTarget.value.id)
  }
  deleteTarget.value = null
}

function menuItems(session: ChatSession) {
  return [[
    { label: 'Umbenennen', icon: 'i-lucide-pencil', onSelect: () => startRename(session) },
    { label: 'Löschen', icon: 'i-lucide-trash-2', color: 'error' as const, onSelect: () => requestDelete(session) }
  ]]
}
</script>

<template>
  <nav class="flex flex-col h-full w-full gap-2 p-2">
    <UButton
      icon="i-lucide-plus"
      label="Neuer Chat"
      variant="soft"
      color="neutral"
      block
      @click="emit('create')"
    />

    <ul class="flex-1 min-h-0 overflow-y-auto flex flex-col gap-0.5">
      <li
        v-for="session in props.sessions"
        :key="session.id"
      >
        <div
          class="group flex items-center gap-1 rounded-md px-2 py-1.5 cursor-pointer"
          :class="session.id === props.activeSessionId ? 'bg-elevated text-highlighted' : 'hover:bg-elevated/60 text-muted'"
          @click="emit('switch', session.id)"
        >
          <UInput
            v-if="editingId === session.id"
            v-model="editingTitle"
            size="xs"
            autofocus
            class="flex-1"
            @click.stop
            @keyup.enter="confirmRename"
            @keyup.escape="cancelRename"
            @blur="confirmRename"
          />
          <span
            v-else
            class="flex-1 truncate text-sm"
          >
            {{ sessionLabel(session) }}
          </span>

          <UDropdownMenu
            v-if="editingId !== session.id"
            :items="menuItems(session)"
          >
            <UButton
              icon="i-lucide-ellipsis"
              variant="ghost"
              color="neutral"
              size="xs"
              class="opacity-0 group-hover:opacity-100"
              @click.stop
            />
          </UDropdownMenu>
        </div>
      </li>
    </ul>

    <UModal
      :open="deleteTarget !== null"
      :title="`&quot;${deleteTarget ? sessionLabel(deleteTarget) : ''}&quot; löschen?`"
      @update:open="(value: boolean) => { if (!value) deleteTarget = null }"
    >
      <template #body>
        <div class="flex flex-col gap-3">
          <p class="text-sm text-muted">
            Dieser Chat wird endgültig gelöscht und kann nicht wiederhergestellt werden.
          </p>
          <div class="flex justify-end gap-2">
            <UButton
              variant="ghost"
              color="neutral"
              label="Abbrechen"
              @click="deleteTarget = null"
            />
            <UButton
              color="error"
              label="Löschen"
              @click="confirmDelete"
            />
          </div>
        </div>
      </template>
    </UModal>
  </nav>
</template>
