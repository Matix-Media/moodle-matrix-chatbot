// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui',
    // Renders assistant answers (which contain **bold**, links, lists) as
    // real formatted output via <MDC :value="..."> instead of literal
    // markdown syntax — see app/pages/index.vue.
    '@nuxtjs/mdc'
  ],

  devtools: {
    enabled: true
  },

  css: ['~/assets/css/main.css'],

  // Server-only — never exposed to the client bundle (bsbot-matrix-chatbot
  // spec 014). Read via useRuntimeConfig() inside server/api routes only.
  runtimeConfig: {
    // The public token end users must supply to use the chat (?token=... or
    // the prompt shown when it's missing/wrong). See server/api/chat.post.ts.
    chatAccessToken: '',
    // Internal bsbot HTTP API this server calls into.
    bsbotApiUrl: 'http://api:8000',
    bsbotApiToken: ''
  },

  compatibilityDate: '2026-06-30',

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  }
})
