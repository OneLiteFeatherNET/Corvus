import { describe, expect, it } from 'vitest'
import { parseTheme, resolveTheme } from './theme'

describe('theme', () => {
  it('prefers the stored choice over the system preference', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('follows the system preference without a choice', () => {
    expect(resolveTheme(null, true)).toBe('dark')
    expect(resolveTheme(undefined, false)).toBe('light')
  })

  it('ignores unknown stored values', () => {
    expect(parseTheme('purple')).toBeNull()
    expect(resolveTheme('purple', true)).toBe('dark')
  })
})
