import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
// @ts-expect-error plain ESM script without type declarations
import { buildTokensCss, TOKENS_PATH } from './m3-tokens.mjs'

const css: string = buildTokensCss()

describe('m3 tokens', () => {
  it('matches the committed file (run pnpm tokens after changing the generator)', () => {
    expect(readFileSync(TOKENS_PATH, 'utf8')).toBe(css)
  })

  it.each(['primary', 'on-primary', 'primary-container', 'surface', 'surface-container-high', 'on-surface', 'outline-variant', 'error', 'inverse-surface', 'success', 'warning-container'])(
    'defines the %s role for both schemes',
    (role) => {
      const matches = css.match(new RegExp(`--md-sys-color-${role}:`, 'g')) ?? []
      // light + system-dark media query + explicit dark
      expect(matches.length).toBe(3)
    },
  )

  it('maps every role into the Tailwind theme', () => {
    expect(css).toContain('--color-primary-container: var(--md-sys-color-primary-container);')
  })
})
