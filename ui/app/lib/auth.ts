import { UserManager, WebStorageStateStore } from 'oidc-client-ts'
import type { User } from 'oidc-client-ts'
import type { AppConfig } from './config'
import { decodeJwtPayload, rolesFromClaims, ROLES } from './roles'
import type { Role } from '~/types'

export interface Session {
  token: string
  roles: Role[]
  subject: string
}

/** Login mechanism behind the UI. The API validates every token again; this only feeds the UI. */
export interface AuthProvider {
  /** Restores a stored session, if there is one. */
  init: () => Promise<Session | null>
  /** Starts a login. For OIDC this navigates away; the returned promise rarely resolves. */
  login: (returnTo: string, input?: { token?: string, roles?: Role[] }) => Promise<void>
  /** Finishes the redirect at /auth/callback and returns the path to continue at. */
  completeLogin: () => Promise<string>
  logout: () => Promise<void>
  /** Registers a listener for session changes made outside this tab (login, renewal, logout in another tab). */
  onChange: (cb: (s: Session | null) => void) => () => void
}

function join(basePath: string, path: string): string {
  return `${basePath.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

export function sessionFromUser(user: User | null, cfg: AppConfig['auth']): Session | null {
  if (!user || user.expired || !user.access_token) return null
  const claims: Record<string, unknown> = {
    ...decodeJwtPayload(user.access_token),
    ...(user.profile as unknown as Record<string, unknown>),
  }
  return {
    token: user.access_token,
    roles: rolesFromClaims(claims, cfg.rolesClaim, cfg.roleMapping),
    subject: String(user.profile.preferred_username ?? user.profile.name ?? user.profile.sub ?? ''),
  }
}

/** Authorization Code + PKCE. The user is kept in localStorage so every tab shares the login. */
export function createOidcAuth(cfg: AppConfig, origin: string, basePath: string, storage: Storage): AuthProvider {
  const manager = new UserManager({
    authority: cfg.oidc.issuer,
    client_id: cfg.oidc.clientId,
    redirect_uri: `${origin}${join(basePath, 'auth/callback')}`,
    post_logout_redirect_uri: `${origin}${basePath}`,
    response_type: 'code',
    scope: cfg.oidc.scopes,
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: storage }),
    stateStore: new WebStorageStateStore({ store: storage }),
  })

  return {
    async init() {
      return sessionFromUser(await manager.getUser(), cfg.auth)
    },
    async login(returnTo) {
      await manager.signinRedirect({ state: { returnTo } })
    },
    async completeLogin() {
      const user = await manager.signinRedirectCallback()
      const state = user.state as { returnTo?: unknown } | undefined
      const to = typeof state?.returnTo === 'string' ? state.returnTo : '/'
      // Only same-app paths; a crafted state must not turn the callback into an open redirect.
      return to.startsWith('/') && !to.startsWith('//') ? to : '/'
    },
    async logout() {
      await manager.signoutRedirect().catch(async () => {
        await manager.removeUser()
      })
    },
    onChange(cb) {
      const load = (u: User) => cb(sessionFromUser(u, cfg.auth))
      const unload = () => cb(null)
      manager.events.addUserLoaded(load)
      manager.events.addUserUnloaded(unload)
      manager.events.addAccessTokenExpired(unload)
      return () => {
        manager.events.removeUserLoaded(load)
        manager.events.removeUserUnloaded(unload)
        manager.events.removeAccessTokenExpired(unload)
      }
    },
  }
}

const TOKEN_KEY = 'corvus.dev.session'

/** Development login: paste the API's static dev token and say which roles the UI should assume. */
export function createTokenAuth(storage: Storage): AuthProvider {
  const listeners = new Set<(s: Session | null) => void>()
  const read = (): Session | null => {
    try {
      const raw = storage.getItem(TOKEN_KEY)
      if (!raw) return null
      const v = JSON.parse(raw) as Partial<Session>
      if (typeof v.token !== 'string' || !Array.isArray(v.roles)) return null
      return { token: v.token, roles: v.roles.filter((r): r is Role => ROLES.includes(r)), subject: 'dev-token' }
    }
    catch {
      return null
    }
  }
  return {
    init: () => Promise.resolve(read()),
    login(_returnTo, input) {
      const session: Session = { token: input?.token ?? '', roles: input?.roles ?? [], subject: 'dev-token' }
      storage.setItem(TOKEN_KEY, JSON.stringify(session))
      listeners.forEach(l => l(session))
      return Promise.resolve()
    },
    completeLogin: () => Promise.resolve('/'),
    logout() {
      storage.removeItem(TOKEN_KEY)
      listeners.forEach(l => l(null))
      return Promise.resolve()
    },
    onChange(cb) {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
  }
}
