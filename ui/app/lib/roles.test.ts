import { describe, expect, it } from 'vitest'
import { visibleNav, requirementFor } from './nav'
import { can, decodeJwtPayload, REQUIRED_ROLES, rolesFromClaims } from './roles'
import type { Action } from './roles'
import type { Role } from '~/types'

const actions = Object.keys(REQUIRED_ROLES) as Action[]

describe('can', () => {
  it('lets admin do everything', () => {
    for (const a of actions) expect(can(['admin'], a), a).toBe(true)
  })

  it('does not treat roles as hierarchical', () => {
    expect(can(['staff'], 'labels.create')).toBe(true)
    expect(can(['staff'], 'labels.import')).toBe(false)
    expect(can(['data_scientist'], 'labels.create')).toBe(false)
    expect(can(['data_scientist'], 'labels.import')).toBe(true)
  })

  it('limits viewers to the read endpoints', () => {
    const allowed = actions.filter(a => can(['viewer'], a)).sort()
    expect(allowed).toEqual(['labels.read', 'models.read'])
  })

  it('reserves players and the blocklist for admin', () => {
    for (const r of ['viewer', 'staff', 'data_scientist'] as Role[]) {
      expect(can([r], 'players.manage')).toBe(false)
      expect(can([r], 'blocklist.manage')).toBe(false)
    }
  })

  it('allows an action when any held role grants it', () => {
    expect(can(['viewer', 'data_scientist'], 'pools.write')).toBe(true)
  })

  it('denies everything without roles', () => {
    for (const a of actions) expect(can([], a)).toBe(false)
  })
})

describe('navigation gating', () => {
  const titles = (roles: Role[]) => visibleNav(a => can(roles, a)).map(i => i.key)

  it('shows only dashboard, models and labels to a viewer', () => {
    expect(titles(['viewer'])).toEqual(['dashboard', 'models', 'labels'])
  })

  it('shows everything to admin', () => {
    expect(titles(['admin'])).toHaveLength(9)
  })

  it('shows the score view to staff and admin only', () => {
    expect(titles(['staff'])).toContain('scores')
    expect(titles(['admin'])).toContain('scores')
    for (const r of ['viewer', 'data_scientist'] as Role[]) expect(titles([r])).not.toContain('scores')
  })

  it('guards the score route with the scores action', () => {
    expect(requirementFor('/scores')).toBe('scores.read')
    expect(can(['staff'], 'scores.read')).toBe(true)
    expect(can(['viewer'], 'scores.read')).toBe(false)
    expect(can(['data_scientist'], 'scores.read')).toBe(false)
  })

  it('maps nested routes to the action they need', () => {
    expect(requirementFor('/pools/abc/edit')).toBe('pools.read')
    expect(requirementFor('/players')).toBe('players.manage')
    expect(requirementFor('/')).toBeUndefined()
    expect(requirementFor('/poolsx')).toBeUndefined()
  })
})

describe('rolesFromClaims', () => {
  it('reads a list claim and ignores unknown values', () => {
    expect(rolesFromClaims({ roles: ['admin', 'wizard'] }, 'roles')).toEqual(['admin'])
  })

  it('reads a space separated string', () => {
    expect(rolesFromClaims({ roles: 'staff viewer' }, 'roles')).toEqual(['staff', 'viewer'])
  })

  it('follows dotted paths', () => {
    expect(rolesFromClaims({ realm_access: { roles: ['data_scientist'] } }, 'realm_access.roles')).toEqual(['data_scientist'])
  })

  it('applies the role mapping', () => {
    expect(rolesFromClaims({ groups: ['corvus-admins'] }, 'groups', { 'corvus-admins': 'admin' })).toEqual(['admin'])
  })

  it('returns no roles for a missing claim and removes duplicates', () => {
    expect(rolesFromClaims({}, 'roles')).toEqual([])
    expect(rolesFromClaims({ roles: ['admin', 'admin'] }, 'roles')).toEqual(['admin'])
  })
})

describe('decodeJwtPayload', () => {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

  it('decodes the payload segment', () => {
    expect(decodeJwtPayload(`h.${b64({ roles: ['admin'], name: 'Zoe' })}.s`)).toEqual({ roles: ['admin'], name: 'Zoe' })
  })

  it('returns an empty object for garbage', () => {
    expect(decodeJwtPayload('nonsense')).toEqual({})
    expect(decodeJwtPayload('a.!!!.c')).toEqual({})
  })
})
