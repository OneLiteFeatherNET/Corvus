const HEX = /^[0-9a-f]+$/i

/** Pseudonyms are 64 hex characters, session ids 32. */
export function isValidSubject(type: 'pseudonym' | 'session', subject: string): boolean {
  const s = subject.trim()
  return s.length === (type === 'pseudonym' ? 64 : 32) && HEX.test(s)
}
