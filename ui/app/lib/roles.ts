import type { Role } from '~/types'

export const ROLES: readonly Role[] = ['admin', 'data_scientist', 'staff', 'viewer']

export type Action =
  | 'pools.read' | 'pools.write'
  | 'snapshots.read' | 'snapshots.create'
  | 'jobs.read' | 'jobs.create'
  | 'models.read' | 'models.transition'
  | 'labels.read' | 'labels.create' | 'labels.import'
  | 'players.manage' | 'blocklist.manage'
  | 'scores.read'

const READ: readonly Role[] = ['viewer', 'staff', 'data_scientist', 'admin']
const DS: readonly Role[] = ['data_scientist', 'admin']

/** Mirrors docs/reference/control-plane-api.md. `admin` is allowed everywhere; the other roles are not hierarchical. */
export const REQUIRED_ROLES: Record<Action, readonly Role[]> = {
  'pools.read': DS,
  'pools.write': DS,
  'snapshots.read': DS,
  'snapshots.create': DS,
  'jobs.read': DS,
  'jobs.create': DS,
  'models.read': READ,
  'models.transition': DS,
  'labels.read': READ,
  'labels.create': ['staff', 'admin'],
  'labels.import': DS,
  'players.manage': ['admin'],
  'blocklist.manage': ['admin'],
  'scores.read': ['staff', 'admin'],
}

export function can(roles: readonly Role[], action: Action): boolean {
  return REQUIRED_ROLES[action].some(r => roles.includes(r))
}

function isRole(v: string): v is Role {
  return (ROLES as readonly string[]).includes(v)
}

function claimAt(claims: Record<string, unknown>, path: string): unknown {
  if (path in claims) return claims[path]
  let cur: unknown = claims
  for (const part of path.split('.')) {
    if (typeof cur !== 'object' || cur === null) return undefined
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur
}

/** Reads roles like the API does: list or space-separated string, optional mapping, unknown values ignored. */
export function rolesFromClaims(
  claims: Record<string, unknown>,
  claim: string,
  mapping: Record<string, string> = {},
): Role[] {
  const raw = claimAt(claims, claim)
  let values: string[] = []
  if (Array.isArray(raw)) values = raw.filter((v): v is string => typeof v === 'string')
  else if (typeof raw === 'string') values = raw.split(/\s+/).filter(Boolean)
  const mapped = values.map(v => mapping[v] ?? v).filter(isRole)
  return [...new Set(mapped)]
}

/** Decodes the payload of a JWT without verifying it. For presentation only: the API enforces the roles. */
export function decodeJwtPayload(token: string): Record<string, unknown> {
  const part = token.split('.')[1]
  if (!part) return {}
  try {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, '='))
        .split('')
        .map(c => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    )
    const parsed: unknown = JSON.parse(json)
    return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : {}
  }
  catch {
    return {}
  }
}
