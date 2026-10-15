/** The word an admin has to type before an erasure request is sent. */
export const ERASE_WORD = 'ERASE'

const UUID_RE = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i
const PSEUDONYM_RE = /^[0-9a-f]{64}$/i

export function isUuid(v: string): boolean {
  return UUID_RE.test(v.trim())
}

export function isPseudonym(v: string): boolean {
  return PSEUDONYM_RE.test(v.trim())
}

/** Exact, case-sensitive match so that a stray "erase" or an autocomplete cannot confirm. */
export function isErasureConfirmed(typed: string): boolean {
  return typed.trim() === ERASE_WORD
}

export type ErasureTarget = { uuid: string, pseudonym?: undefined } | { pseudonym: string, uuid?: undefined }

/**
 * Builds the request body once the guard passes. Returns null while the confirmation word is wrong or the
 * identifier is malformed, so callers cannot send a request by accident.
 */
export function buildErasureRequest(identifier: string, typed: string): ErasureTarget | null {
  if (!isErasureConfirmed(typed)) return null
  const id = identifier.trim()
  if (isPseudonym(id)) return { pseudonym: id.toLowerCase() }
  if (isUuid(id)) return { uuid: id }
  return null
}

/** First 12 hex characters, the same truncation the API uses in its logs. */
export function shortPseudonym(p: string): string {
  return p.slice(0, 12)
}
