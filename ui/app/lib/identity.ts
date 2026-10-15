import type { ApiClient } from './api-client'
import { ROLES } from './roles'
import type { Session } from './auth'
import type { Me, Role } from '~/types'

/**
 * The API is the source of truth for roles (GET /me, after its role mapping). The roles read from the token claim
 * stay as the fallback when /me cannot be reached, so the UI still works against an older API.
 */
export async function withServerIdentity(api: Pick<ApiClient, 'get'>, session: Session): Promise<Session> {
  try {
    const me = await api.get<Me>('/me')
    const roles = me.roles.filter((r): r is Role => (ROLES as readonly string[]).includes(r))
    return { ...session, roles, subject: me.display_name || me.subject || session.subject }
  }
  catch {
    return session
  }
}
