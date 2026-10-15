import { createVuetify } from 'vuetify'
import { md3 } from 'vuetify/blueprints'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import type { ThemeDefinition } from 'vuetify'

// OneLiteFeather Material 3 tokens, generated from OLF Pink #EC008B (Vibrant scheme).
const light: ThemeDefinition = {
  dark: false,
  colors: {
    'primary': '#AC1C67',
    'on-primary': '#FFEFF2',
    'primary-container': '#FF6EAC',
    'on-primary-container': '#4B0028',
    'secondary': '#94387C',
    'on-secondary': '#FFEEF5',
    'secondary-container': '#FFBFE6',
    'on-secondary-container': '#7B2266',
    'tertiary': '#4D4FB7',
    'on-tertiary': '#F4F1FF',
    'tertiary-container': '#A7A9FF',
    'on-tertiary-container': '#1F1D8A',
    'error': '#B41340',
    'on-error': '#FFEFEF',
    'error-container': '#F74B6D',
    'on-error-container': '#510017',
    'background': '#FFF4F7',
    'on-background': '#47223C',
    'surface': '#FFF4F7',
    'on-surface': '#47223C',
    'surface-variant': '#FFCFEB',
    'on-surface-variant': '#794E6A',
    'surface-bright': '#FFF4F7',
    'surface-light': '#FFECF4',
    'surface-container-lowest': '#FFFFFF',
    'surface-container-low': '#FFECF4',
    'surface-container': '#FFE0F0',
    'surface-container-high': '#FFD8ED',
    'surface-container-highest': '#FFCFEB',
    'outline': '#976986',
    'outline-variant': '#D29EBD',
    'info': '#4D4FB7',
    'success': '#94387C',
    'warning': '#94387C',
  },
}

const dark: ThemeDefinition = {
  dark: true,
  colors: {
    'primary': '#FF88B7',
    'on-primary': '#600036',
    'primary-container': '#FF6EAC',
    'on-primary-container': '#4B0028',
    'secondary': '#F88CD6',
    'on-secondary': '#5F024D',
    'secondary-container': '#7B2166',
    'on-secondary-container': '#FFBDE5',
    'tertiary': '#9496FF',
    'on-tertiary': '#0D037E',
    'tertiary-container': '#8688F4',
    'on-tertiary-container': '#050054',
    'error': '#FF6E84',
    'on-error': '#490013',
    'error-container': '#A70138',
    'on-error-container': '#FFB2B9',
    'background': '#210219',
    'on-background': '#FFDCEF',
    'surface': '#210219',
    'on-surface': '#FFDCEF',
    'surface-variant': '#431336',
    'on-surface-variant': '#D09CBB',
    'surface-bright': '#4C183E',
    'surface-light': '#310927',
    'surface-container-lowest': '#000000',
    'surface-container-low': '#280520',
    'surface-container': '#310927',
    'surface-container-high': '#3A0E2F',
    'surface-container-highest': '#431336',
    'outline': '#966785',
    'outline-variant': '#633B56',
    'info': '#9496FF',
    'success': '#F88CD6',
    'warning': '#F88CD6',
  },
}

export default defineNuxtPlugin((nuxtApp) => {
  const stored = (() => {
    try {
      return localStorage.getItem('corvus.theme')
    }
    catch {
      return null
    }
  })()
  const system = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'

  const vuetify = createVuetify({
    blueprint: md3,
    icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
    theme: {
      defaultTheme: stored === 'dark' || stored === 'light' ? stored : system,
      themes: { light, dark },
    },
    defaults: {
      VTextField: { variant: 'outlined', density: 'comfortable' },
      VSelect: { variant: 'outlined', density: 'comfortable' },
      VCombobox: { variant: 'outlined', density: 'comfortable' },
      VTextarea: { variant: 'outlined', density: 'comfortable' },
    },
  })
  nuxtApp.vueApp.use(vuetify)
})
