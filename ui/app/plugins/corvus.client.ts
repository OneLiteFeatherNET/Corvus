import { createApiClient } from '~/lib/api-client'
import type { ApiClient } from '~/lib/api-client'
import { createOidcAuth, createTokenAuth } from '~/lib/auth'
import type { AuthProvider, Session } from '~/lib/auth'
import { readBasePath } from '~/lib/base'
import { withServerIdentity } from '~/lib/identity'
import { ConfigError, loadConfig } from '~/lib/config'
import type { AppConfig } from '~/lib/config'

declare module '#app' {
  interface NuxtApp {
    $corvus: { config: AppConfig, auth: AuthProvider, api: ApiClient }
  }
}

// Loads /config.json first, then builds the auth provider and the API client from it. Runs before the first
// navigation, so route middleware can rely on the session.
export default defineNuxtPlugin({
  name: 'corvus',
  async setup() {
    const configState = useState<AppConfig | null>('corvus.config', () => null)
    const errorState = useState<string | null>('corvus.config-error', () => null)
    const session = useState<Session | null>('corvus.session', () => null)

    const basePath = readBasePath()
    let config: AppConfig
    try {
      config = await loadConfig((u, i) => fetch(u, i), `${basePath}config.json`)
    }
    catch (e) {
      errorState.value = e instanceof ConfigError ? e.message : 'config.json could not be loaded'
      return {}
    }
    configState.value = config

    const auth = config.auth.mode === 'oidc'
      ? createOidcAuth(config, location.origin, basePath, localStorage)
      : createTokenAuth(localStorage)

    let redirecting = false
    const api = createApiClient({
      baseUrl: config.apiBaseUrl,
      fetch: (u, i) => fetch(u, i),
      getToken: () => session.value?.token ?? null,
      onUnauthorized: () => {
        if (redirecting) return
        redirecting = true
        session.value = null
        const here = useRouter().currentRoute.value.fullPath
        if (config.auth.mode === 'oidc') void auth.login(here)
        else void useRouter().push({ path: '/login', query: { next: here } }).finally(() => (redirecting = false))
      },
    })

    // The token claims give the roles at once; GET /me then replaces them with the roles the API sees. If /me
    // fails, the claim roles stay.
    const adopt = async (s: Session | null) => {
      session.value = s
      if (!s) return
      const resolved = await withServerIdentity(api, s)
      if (session.value?.token === s.token) session.value = resolved
    }

    await adopt(await auth.init().catch(() => null))
    auth.onChange((s) => {
      void adopt(s)
    })
    // Another tab logged in or out (the token login and the OIDC user both live in localStorage).
    window.addEventListener('storage', () => {
      void auth.init().then(adopt).catch(() => {})
    })

    return { provide: { corvus: { config, auth, api } } }
  },
})
