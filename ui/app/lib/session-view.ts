import type { Session } from '~/types'

/** The first `n` characters of an id, followed by an ellipsis when something was cut off. */
export function shortHex(value: string, n = 16): string {
  return value.length > n ? `${value.slice(0, n)}…` : value
}

/** Distinct game contexts of a session's segments, in the order they first appear. */
export function sessionGameContexts(session: Pick<Session, 'segments'>): string[] {
  return [...new Set(session.segments.map(s => s.game_context))]
}
