import { requirementFor } from '~/lib/nav'
import { can } from '~/lib/roles'
import type { Session } from '~/lib/auth'

const OPEN_ROUTES = ['/auth/callback', '/login', '/forbidden']

export default defineNuxtRouteMiddleware(async (to) => {
  if (import.meta.server) return
  const failed = useState<string | null>('corvus.config-error', () => null)
  if (failed.value) return // app.vue renders the configuration error

  if (OPEN_ROUTES.includes(to.path)) return

  const session = useState<Session | null>('corvus.session', () => null)
  if (!session.value) {
    const { $corvus } = useNuxtApp()
    if ($corvus.config.auth.mode === 'oidc') {
      await $corvus.auth.login(to.fullPath)
      return abortNavigation()
    }
    return navigateTo({ path: '/login', query: { next: to.fullPath } })
  }

  const needed = requirementFor(to.path)
  if (needed && !can(session.value.roles, needed)) return navigateTo('/forbidden')
})
