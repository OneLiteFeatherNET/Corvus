import { describe, expect, it } from 'vitest'
import { parseCsv, validateLabelCsv } from './label-csv'
import { isValidSubject } from './label-form'

const pseud = 'a'.repeat(64)
const sess = 'b'.repeat(32)
const header = 'subject_type,subject,label,source,confidence'

describe('parseCsv', () => {
  it('handles quotes, doubled quotes, CRLF and a BOM', () => {
    expect(parseCsv('﻿a,"b,c","d ""e"""\r\n1,2,3\r\n')).toEqual([['a', 'b,c', 'd "e"'], ['1', '2', '3']])
  })

  it('keeps a trailing line without newline and skips blank lines', () => {
    expect(parseCsv('a,b\n\n1,2')).toEqual([['a', 'b'], ['1', '2']])
  })
})

describe('validateLabelCsv', () => {
  it('accepts valid rows with optional columns', () => {
    const csv = `${header},valid_from,note\npseudonym,${pseud},cheat,staff_verdict,0.9,2026-09-01T00:00:00Z,"seen, flagged"\nsession,${sess},legit,honeypot,1,,\n`
    const p = validateLabelCsv(csv)
    expect(p.valid).toBe(true)
    expect(p.rowCount).toBe(2)
    expect(p.issues).toEqual([])
  })

  it('reports missing and unknown columns on line 1', () => {
    const p = validateLabelCsv('subject,label,colour\nx,y,z\n')
    expect(p.valid).toBe(false)
    expect(p.issues.filter(i => i.code === 'missingColumn').map(i => i.column)).toEqual(['subject_type', 'source', 'confidence'])
    expect(p.issues).toContainEqual({ line: 1, code: 'unknownColumn', column: 'colour' })
  })

  it('points at the failing line and column', () => {
    const csv = `${header}\npseudonym,${pseud},cheat,weak_rule,0.5\npseudonym,short,cheat,weak_rule,0.5\nsession,${sess},maybe,nope,2\n`
    const p = validateLabelCsv(csv)
    expect(p.valid).toBe(false)
    expect(p.issues).toEqual([
      { line: 3, code: 'badSubject', column: 'subject' },
      { line: 4, code: 'badLabel', column: 'label' },
      { line: 4, code: 'badSource', column: 'source' },
      { line: 4, code: 'badConfidence', column: 'confidence' },
    ])
  })

  it('checks the validity window', () => {
    const base = `${header},valid_from,valid_to\n`
    expect(validateLabelCsv(`${base}pseudonym,${pseud},cheat,weak_rule,1,2026-02-01T00:00:00Z,2026-01-01T00:00:00Z`).issues)
      .toContainEqual({ line: 2, code: 'dateOrder', column: 'valid_from' })
    expect(validateLabelCsv(`${base}pseudonym,${pseud},cheat,weak_rule,1,2026-02-01,`).issues)
      .toContainEqual({ line: 2, code: 'badDate', column: 'valid_from' })
  })

  it('rejects empty files, header-only files and oversized input', () => {
    expect(validateLabelCsv('').valid).toBe(false)
    expect(validateLabelCsv(`${header}\n`).valid).toBe(false)
    expect(validateLabelCsv(`${header}\n`, 9 * 1024 * 1024).issues).toContainEqual({ line: 0, code: 'tooLarge' })
  })
})

describe('isValidSubject', () => {
  it('uses 64 hex for pseudonyms and 32 for sessions', () => {
    expect(isValidSubject('pseudonym', pseud)).toBe(true)
    expect(isValidSubject('pseudonym', sess)).toBe(false)
    expect(isValidSubject('session', sess)).toBe(true)
    expect(isValidSubject('session', `${'z'.repeat(32)}`)).toBe(false)
  })
})
