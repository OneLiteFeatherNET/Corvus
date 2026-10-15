const KEY = 'corvus.erasure-requests'
const MAX = 50

/**
 * The API has no list endpoint for erasure requests, only `GET /players/erasure/{id}`. The UI therefore remembers
 * the ids of requests made from this browser. Only random request ids are stored, never a UUID or pseudonym.
 */
export function readErasureIds(storage: Pick<Storage, 'getItem'>): string[] {
  try {
    const raw = storage.getItem(KEY)
    const v: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, MAX) : []
  }
  catch {
    return []
  }
}

export function rememberErasureId(storage: Pick<Storage, 'getItem' | 'setItem'>, id: string): string[] {
  const ids = [id, ...readErasureIds(storage).filter(x => x !== id)].slice(0, MAX)
  try {
    storage.setItem(KEY, JSON.stringify(ids))
  }
  catch { /* storage full or blocked: the list just is not kept */ }
  return ids
}
