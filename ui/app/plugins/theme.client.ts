// Applies an explicit theme choice before the first paint of the app; without one the CSS follows the system.
export default defineNuxtPlugin(() => {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const { restore, theme } = useColorTheme()
  restore()
  media.addEventListener('change', (e) => {
    if (!document.documentElement.dataset.theme) theme.value = e.matches ? 'dark' : 'light'
  })
})
