export type ThemeName = 'light' | 'dark'

export const THEME_KEY = 'corvus.theme'

export function parseTheme(value: string | null | undefined): ThemeName | null {
  return value === 'light' || value === 'dark' ? value : null
}

/** The theme to show: an explicit choice wins over the system preference. */
export function resolveTheme(stored: string | null | undefined, systemDark: boolean): ThemeName {
  return parseTheme(stored) ?? (systemDark ? 'dark' : 'light')
}
