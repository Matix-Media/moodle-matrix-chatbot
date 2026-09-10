<script setup lang="ts">
// Stands in for MDC's default `a` tag renderer (see nuxt.config.ts's
// `mdc.components.map`). A plain link falls through to a normal <a>; a
// "cite://ref?c=..." link (built by citationsToLinks(), see
// app/utils/citationLinks.ts) renders as a Google-AI-Overview-style source
// chip instead — icon + source name, "+N" when a statement cites more than
// one source — with a hover card listing every grouped source.
const props = defineProps<{
  href?: string
  class?: unknown
}>()

const group = computed(() => {
  if (!props.href?.startsWith('cite://')) return null
  try {
    const parsed = new URL(props.href)
    const raw = parsed.searchParams.get('c')
    if (!raw) return null
    const entries = JSON.parse(raw) as CitationLinkEntry[]
    return entries.length ? entries : null
  } catch {
    return null
  }
})

const primary = computed(() => group.value?.[0] ?? null)
const extraCount = computed(() => (group.value ? group.value.length - 1 : 0))
const label = computed(() =>
  primary.value ? primary.value.l + (extraCount.value > 0 ? ` +${extraCount.value}` : '') : ''
)
</script>

<template>
  <a
    v-if="!group"
    :href="href"
    target="_blank"
    rel="noopener noreferrer"
    :class="props.class"
  ><slot /></a>

  <UPopover
    v-else
    mode="hover"
    :open-delay="150"
    :close-delay="100"
    :content="{ side: 'top', sideOffset: 6 }"
  >
    <UBadge
      as="a"
      :href="primary?.u ?? undefined"
      :target="primary?.u ? '_blank' : undefined"
      :rel="primary?.u ? 'noopener noreferrer' : undefined"
      :icon="primary?.i"
      :label="label"
      color="neutral"
      variant="subtle"
      size="sm"
      class="citation-chip"
      :class="{ 'citation-chip--inactive': !primary?.u }"
      @click="(e: MouseEvent) => { if (!primary?.u) e.preventDefault() }"
    />

    <template #content>
      <div class="citation-card">
        <div
          v-for="(entry, i) in group"
          :key="i"
          class="citation-card__entry"
        >
          <div class="citation-card__header">
            <UIcon
              :name="entry.i"
              class="size-3.5 shrink-0"
            />
            <span>{{ entry.l }}</span>
          </div>
          <p class="citation-card__title">
            {{ entry.t }}
          </p>
          <p
            v-if="entry.u"
            class="citation-card__url"
          >
            {{ entry.u }}
          </p>
        </div>
      </div>
    </template>
  </UPopover>
</template>

<style scoped>
.citation-chip {
  vertical-align: middle;
  margin: 0 0.15em;
  cursor: pointer;
  max-width: 12rem;
}
.citation-chip,
.citation-chip:hover {
  /* .chat-markdown's `:deep(a)` rule (see pages/index.vue) styles every link
     as blue + underlined for prose — override it so the chip keeps its own
     neutral pill look instead of reading as a text link. */
  color: inherit !important;
  text-decoration: none !important;
}
.citation-chip :deep([data-slot='label']) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.citation-chip--inactive {
  cursor: default;
  opacity: 0.7;
}

.citation-card {
  max-width: 20rem;
  padding: 0.4rem 0;
  font-size: 0.8rem;
}
.citation-card__entry {
  padding: 0.35rem 0.75rem;
}
.citation-card__entry + .citation-card__entry {
  border-top: 1px solid var(--ui-border);
}
.citation-card__header {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-weight: 600;
  color: var(--ui-text-highlighted);
  margin-bottom: 0.25rem;
}
.citation-card__title {
  color: var(--ui-text);
  margin: 0;
}
.citation-card__url {
  color: var(--ui-text-muted);
  margin: 0.25rem 0 0;
  word-break: break-all;
}
</style>
