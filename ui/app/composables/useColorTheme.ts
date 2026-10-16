import { parseTheme, resolveTheme, THEME_KEY } from '~/lib/theme'
import type { ThemeName } from '~/lib/theme'

function stored(): string | null {
  try {
    return localStorage.getItem(THEME_KEY)
  }
  catch {
    return null
  }
}

/**
 * Light/dark theme. Without an explicit choice the page follows the system preference through CSS
 * (m3-tokens.css); the toggle writes the choice to the html element and to localStorage.
 */
export function useColorTheme() {
  const theme = useState<ThemeName>('corvus.theme', () => resolveTheme(stored(), window.matchMedia('(prefers-color-scheme: dark)').matches))
  const isDark = computed(() => theme.value === 'dark')

  function apply(next: ThemeName) {
    theme.value = next
    document.documentElement.dataset.theme = next
  }
  function toggle() {
    const next: ThemeName = isDark.value ? 'light' : 'dark'
    apply(next)
    try {
      localStorage.setItem(THEME_KEY, next)
    }
    catch { /* storage unavailable: the choice just does not persist */ }
  }
  /** Restore an explicit choice made earlier (called once at startup). */
  function restore() {
    const choice = parseTheme(stored())
    if (choice) apply(choice)
  }
  return { theme, isDark, toggle, restore }
}
