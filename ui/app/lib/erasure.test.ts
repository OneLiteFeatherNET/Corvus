import { describe, expect, it } from 'vitest'
import { buildErasureRequest, ERASE_WORD, isErasureConfirmed, isPseudonym, isUuid, shortPseudonym } from './erasure'
import { readErasureIds, rememberErasureId } from './erasure-log'

const pseudonym = 'ab'.repeat(32)
const uuid = '069a79f4-44e9-4726-a5be-fca90e38aaf5'

describe('erasure confirmation guard', () => {
  it('requires exactly the word ERASE', () => {
    expect(isErasureConfirmed(ERASE_WORD)).toBe(true)
    expect(isErasureConfirmed(' ERASE ')).toBe(true)
  })

  it.each(['', 'erase', 'Erase', 'ERASE!', 'DELETE', 'ERAS'])('rejects %j', (typed) => {
    expect(isErasureConfirmed(typed)).toBe(false)
  })

  it('builds no request while the word is wrong', () => {
    expect(buildErasureRequest(pseudonym, 'erase')).toBeNull()
    expect(buildErasureRequest(uuid, '')).toBeNull()
  })

  it('builds a pseudonym request once confirmed', () => {
    expect(buildErasureRequest(pseudonym.toUpperCase(), 'ERASE')).toEqual({ pseudonym })
  })

  it('builds a uuid request once confirmed', () => {
    expect(buildErasureRequest(uuid, 'ERASE')).toEqual({ uuid })
  })

  it('builds no request for a malformed identifier even when confirmed', () => {
    expect(buildErasureRequest('not-an-id', 'ERASE')).toBeNull()
    expect(buildErasureRequest('ab'.repeat(31), 'ERASE')).toBeNull()
  })
})

describe('identifier checks', () => {
  it('recognises uuids and pseudonyms', () => {
    expect(isUuid(uuid)).toBe(true)
    expect(isUuid(uuid.replaceAll('-', ''))).toBe(true)
    expect(isUuid('xyz')).toBe(false)
    expect(isPseudonym(pseudonym)).toBe(true)
    expect(isPseudonym(`${pseudonym}0`)).toBe(false)
  })

  it('shortens a pseudonym to the 12 characters the API logs', () => {
    expect(shortPseudonym(pseudonym)).toBe('abababababab')
  })
})

describe('erasure log', () => {
  const memory = () => {
    const data = new Map<string, string>()
    return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) }
  }

  it('remembers ids newest first without duplicates', () => {
    const s = memory()
    rememberErasureId(s, 'a')
    rememberErasureId(s, 'b')
    expect(rememberErasureId(s, 'a')).toEqual(['a', 'b'])
    expect(readErasureIds(s)).toEqual(['a', 'b'])
  })

  it('stores only the request id, never the identifier', () => {
    const s = memory()
    rememberErasureId(s, 'req-1')
    expect(s.getItem('corvus.erasure-requests')).toBe('["req-1"]')
  })

  it('survives corrupt storage', () => {
    expect(readErasureIds({ getItem: () => '{broken' })).toEqual([])
    expect(readErasureIds({ getItem: () => '{"a":1}' })).toEqual([])
  })
})
