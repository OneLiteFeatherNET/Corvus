import { describe, expect, it } from 'vitest'
import { blankLabelInput, isValidSubject } from './label-form'

const session = 'b'.repeat(32)

describe('blankLabelInput', () => {
  it('starts a pseudonym label when no subject is given', () => {
    expect(blankLabelInput()).toMatchObject({ subject_type: 'pseudonym', subject: '' })
  })

  it('prefills the subject type and id of a session', () => {
    expect(blankLabelInput({ type: 'session', id: session })).toMatchObject({ subject_type: 'session', subject: session })
  })

  it('keeps the other defaults when prefilling', () => {
    expect(blankLabelInput({ type: 'session', id: session })).toMatchObject({ label: 'cheat', source: 'staff_verdict', confidence: 1, note: '' })
  })

  it('returns a fresh object on every call so edits do not leak into the next dialog', () => {
    const first = blankLabelInput()
    first.note = 'typed'
    expect(blankLabelInput().note).toBe('')
  })
})

describe('isValidSubject', () => {
  it('accepts 32 hex characters for a session', () => {
    expect(isValidSubject('session', session)).toBe(true)
  })

  it('rejects a session id of pseudonym length', () => {
    expect(isValidSubject('session', 'b'.repeat(64))).toBe(false)
  })
})
