import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'

// Static single-page app. Everything that differs between deployments (API origin, OIDC settings, base path)
// is read from /config.json at startup, never at build time. Asset URLs are relative so that the same build
// works at the root of a domain and under a path prefix (nginx injects <base href>, see docker/).
export default defineNuxtConfig({
  compatibilityDate: '2026-10-01',
  ssr: false,
  devtools: { enabled: false },
  telemetry: false,

  modules: [
    '@nuxt/eslint',
    '@nuxtjs/i18n',
    (_options, nuxt) => {
      nuxt.hooks.hook('vite:extendConfig', (config) => {
        config.plugins!.push(vuetify({ autoImport: true }))
      })
    },
  ],

  app: {
    baseURL: './',
    buildAssetsDir: '_nuxt/',
    head: {
      title: 'Corvus',
      htmlAttrs: { lang: 'de' },
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'robots', content: 'noindex, nofollow' },
      ],
      link: [{ rel: 'icon', type: 'image/png', href: './logo.png' }],
    },
  },

  css: [
    '@fontsource/roboto/300.css',
    '@fontsource/roboto/400.css',
    '@fontsource/roboto/500.css',
    '@fontsource/roboto/700.css',
    '@mdi/font/css/materialdesignicons.css',
    '~/assets/main.css',
  ],

  build: { transpile: ['vuetify'] },

  vite: {
    vue: { template: { transformAssetUrls } },
  },

  i18n: {
    strategy: 'no_prefix',
    defaultLocale: 'de',
    locales: [
      { code: 'de', language: 'de-DE', name: 'Deutsch', file: 'de.json' },
      { code: 'en', language: 'en-US', name: 'English', file: 'en.json' },
    ],
    detectBrowserLanguage: {
      useCookie: true,
      cookieKey: 'corvus_locale',
      fallbackLocale: 'de',
      redirectOn: 'root',
    },
  },

  eslint: { config: { stylistic: false } },

  typescript: { strict: true, typeCheck: false },

  nitro: { preset: 'static', prerender: { crawlLinks: false, routes: [] } },
})
