<script setup lang="ts">
useHead({
  meta: [
    { name: 'viewport', content: 'width=device-width, initial-scale=1' }
  ],
  htmlAttrs: {
    lang: 'de'
  }
})

useSeoMeta({
  title: 'Moodle-Chat',
  description: 'Frag den Bot etwas zu deinem Moodle-Kurs.'
})
</script>

<template>
  <UApp>
    <NuxtPage />
  </UApp>
</template>
