import { computed } from 'vue'
import type { Session } from '~/lib/auth'
import { can as canDo } from '~/lib/roles'
import type { Action } from '~/lib/roles'
import type { Role } from '~/types'

export function useAuth() {
  const { $corvus } = useNuxtApp()
  const session = useState<Session | null>('corvus.session', () => null)
  const roles = computed<Role[]>(() => session.value?.roles ?? [])

  return {
    session,
    roles,
    mode: $corvus.config.auth.mode,
    isAuthenticated: computed(() => session.value !== null),
    can: (action: Action) => canDo(roles.value, action),
    login: (returnTo = '/') => $corvus.auth.login(returnTo),
    loginWithToken: (token: string, selected: Role[], returnTo = '/') =>
      $corvus.auth.login(returnTo, { token, roles: selected }),
    completeLogin: async () => {
      const to = await $corvus.auth.completeLogin()
      session.value = await $corvus.auth.init()
      return to
    },
    logout: async () => {
      await $corvus.auth.logout()
      session.value = null
    },
  }
}
