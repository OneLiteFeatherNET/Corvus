import { describe, expect, it } from 'vitest'
import { sessionGameContexts, shortHex } from './session-view'

describe('shortHex', () => {
  it('cuts a long id to 16 characters and marks the cut', () => {
    expect(shortHex('a'.repeat(64))).toBe(`${'a'.repeat(16)}…`)
  })

  it('leaves an id that already fits unchanged', () => {
    expect(shortHex('abc123')).toBe('abc123')
  })

  it('leaves an id of exactly the limit unchanged', () => {
    expect(shortHex('f'.repeat(16))).toBe('f'.repeat(16))
  })
})

describe('sessionGameContexts', () => {
  it('lists each game context once, in first-seen order', () => {
    const segments = [
      { segment: 0, game_context: 'bedwars' },
      { segment: 1, game_context: 'skywars' },
      { segment: 2, game_context: 'bedwars' },
    ]
    expect(sessionGameContexts({ segments })).toEqual(['bedwars', 'skywars'])
  })

  it('returns nothing for a session without segments', () => {
    expect(sessionGameContexts({ segments: [] })).toEqual([])
  })
})
