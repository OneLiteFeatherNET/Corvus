import { describe, expect, it, vi } from 'vitest'
import { createTokenAuth, sessionFromUser } from './auth'
import type { User } from 'oidc-client-ts'

const memory = (): Storage => {
  const data = new Map<string, string>()
  return {
    getItem: k => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: k => void data.delete(k),
    clear: () => data.clear(),
    key: i => [...data.keys()][i] ?? null,
    get length() {
      return data.size
    },
  }
}

describe('token login', () => {
  it('stores the session and restores it on init', async () => {
    const storage = memory()
    const auth = createTokenAuth(storage)
    expect(await auth.init()).toBeNull()
    await auth.login('/', { token: 'dev', roles: ['staff'] })
    expect(await createTokenAuth(storage).init()).toEqual({ token: 'dev', roles: ['staff'], subject: 'dev-token' })
  })

  it('notifies listeners on login and logout', async () => {
    const auth = createTokenAuth(memory())
    const seen = vi.fn()
    auth.onChange(seen)
    await auth.login('/', { token: 't', roles: ['admin'] })
    await auth.logout()
    expect(seen.mock.calls.map(c => (c[0] as { token: string } | null)?.token ?? null)).toEqual(['t', null])
  })

  it('drops unknown roles from a tampered session', async () => {
    const storage = memory()
    storage.setItem('corvus.dev.session', JSON.stringify({ token: 't', roles: ['admin', 'root'] }))
    expect((await createTokenAuth(storage).init())?.roles).toEqual(['admin'])
  })

  it('ignores corrupt storage', async () => {
    const storage = memory()
    storage.setItem('corvus.dev.session', '{nope')
    expect(await createTokenAuth(storage).init()).toBeNull()
  })
})

describe('sessionFromUser', () => {
  const cfg = { mode: 'oidc' as const, rolesClaim: 'roles', roleMapping: {} }
  const user = (over: Partial<User>) => ({
    access_token: 'a.b.c', expired: false, profile: { sub: 's1', roles: ['viewer'], preferred_username: 'kim' }, ...over,
  }) as unknown as User

  it('takes roles and name from the profile', () => {
    expect(sessionFromUser(user({}), cfg)).toEqual({ token: 'a.b.c', roles: ['viewer'], subject: 'kim' })
  })

  it('returns no session for an expired or missing user', () => {
    expect(sessionFromUser(user({ expired: true }), cfg)).toBeNull()
    expect(sessionFromUser(null, cfg)).toBeNull()
  })
})
