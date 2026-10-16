import { describe, expect, it } from 'vitest'
import de from '../../i18n/locales/de.json'
import en from '../../i18n/locales/en.json'
import { ENUMS, enumLabel, humanize } from './enums'
import openapi from '../../openapi.json'

const lookup = (tree: unknown, path: string): unknown =>
  path.split('.').reduce<unknown>((n, p) => (n && typeof n === 'object' ? (n as Record<string, unknown>)[p] : undefined), tree)

describe('enum translations', () => {
  for (const [prefix, values] of Object.entries(ENUMS)) {
    it.each(values)(`${prefix}.%s is translated in de and en`, (value) => {
      expect(typeof lookup(de, `${prefix}.${value}`)).toBe('string')
      expect(typeof lookup(en, `${prefix}.${value}`)).toBe('string')
    })
  }

  it('lists exactly the values of the API enums', () => {
    const s = openapi.components.schemas as Record<string, { enum?: string[], properties?: Record<string, { enum?: string[] }> }>
    expect([...ENUMS['jobs.type']].sort()).toEqual([...s.JobType!.enum!].sort())
    expect([...ENUMS['jobs.status']].sort()).toEqual([...s.JobState!.enum!].sort())
    expect([...ENUMS['models.stage']].sort()).toEqual([...s.Stage!.enum!].sort())
    expect([...ENUMS['blocklist.kinds']].sort()).toEqual([...s.BlockEntryOut!.properties!.kind!.enum!].sort())
    expect([...ENUMS['blocklist.sources']].sort()).toEqual([...s.BlockEntryOut!.properties!.source!.enum!].sort())
  })
})

describe('enumLabel', () => {
  const t = (k: string) => `T(${k})`
  it('uses the translation when there is one', () => {
    expect(enumLabel('jobs.type', 'train', t, () => true)).toBe('T(jobs.type.train)')
  })
  it('humanises an unknown value instead of showing the key', () => {
    expect(enumLabel('blocklist.sources', 'honey_pot', t, () => false)).toBe('Honey pot')
  })
  it('keeps an empty value', () => {
    expect(humanize('')).toBe('')
  })
})
