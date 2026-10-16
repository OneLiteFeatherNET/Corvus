import type { LabelInput } from '~/types'

const HEX = /^[0-9a-f]+$/i

export type SubjectType = 'pseudonym' | 'session'

/** Pseudonyms are 64 hex characters, session ids 32. */
export function isValidSubject(type: SubjectType, subject: string): boolean {
  const s = subject.trim()
  return s.length === (type === 'pseudonym' ? 64 : 32) && HEX.test(s)
}

/** A subject the label dialog starts with, e.g. the session a row in the snapshot list is about. */
export interface LabelSubject {
  type: SubjectType
  id: string
}

/** Initial values of the label dialog; `subject` prefills the subject type and id. */
export function blankLabelInput(subject?: LabelSubject | null): LabelInput {
  return {
    subject_type: subject?.type ?? 'pseudonym',
    subject: subject?.id ?? '',
    label: 'cheat',
    source: 'staff_verdict',
    confidence: 1,
    note: '',
  }
}
