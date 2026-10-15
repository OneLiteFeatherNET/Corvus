export interface AppConfig {
  /** Control-plane API root including the version prefix, e.g. https://corvus.example/api/v1 (no trailing slash). */
  apiBaseUrl: string
  /** Path the app is served under, e.g. "/" or "/corvus/". Always starts and ends with a slash. */
  basePath: string
  auth: {
    /** `oidc`: Authorization Code + PKCE. `token`: paste a bearer token (local development with the API dev token). */
    mode: 'oidc' | 'token'
    /** Claim that carries the roles (dotted paths such as realm_access.roles work). */
    rolesClaim: string
    /** Maps external role names to Corvus roles. */
    roleMapping: Record<string, string>
  }
  oidc: {
    issuer: string
    clientId: string
    scopes: string
  }
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConfigError'
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function str(v: unknown, field: string, required: boolean): string {
  if (v === undefined || v === null || v === '') {
    if (required) throw new ConfigError(`config.json: "${field}" is required`)
    return ''
  }
  if (typeof v !== 'string') throw new ConfigError(`config.json: "${field}" must be a string`)
  return v
}

export function normalizeBasePath(raw: string): string {
  let p = raw.trim()
  if (!p.startsWith('/')) p = `/${p}`
  if (!p.endsWith('/')) p = `${p}/`
  return p.replace(/\/{2,}/g, '/')
}

/** Validates the parsed config.json and fills in defaults. Throws ConfigError with a readable message. */
export function parseConfig(raw: unknown): AppConfig {
  if (!isRecord(raw)) throw new ConfigError('config.json must be a JSON object')

  const apiBaseUrl = str(raw.apiBaseUrl, 'apiBaseUrl', true).replace(/\/+$/, '')
  if (!/^(https?:\/\/|\/)/.test(apiBaseUrl)) {
    throw new ConfigError('config.json: "apiBaseUrl" must be an absolute URL or an absolute path')
  }

  const auth = isRecord(raw.auth) ? raw.auth : {}
  const mode = auth.mode ?? 'oidc'
  if (mode !== 'oidc' && mode !== 'token') {
    throw new ConfigError('config.json: "auth.mode" must be "oidc" or "token"')
  }
  const roleMapping: Record<string, string> = {}
  if (auth.roleMapping !== undefined) {
    if (!isRecord(auth.roleMapping)) throw new ConfigError('config.json: "auth.roleMapping" must be an object')
    for (const [k, v] of Object.entries(auth.roleMapping)) {
      if (typeof v === 'string') roleMapping[k] = v
    }
  }

  const oidcRaw = isRecord(raw.oidc) ? raw.oidc : {}
  const oidc = {
    issuer: str(oidcRaw.issuer, 'oidc.issuer', mode === 'oidc'),
    clientId: str(oidcRaw.clientId, 'oidc.clientId', mode === 'oidc'),
    scopes: str(oidcRaw.scopes, 'oidc.scopes', false) || 'openid profile',
  }

  return {
    apiBaseUrl,
    basePath: normalizeBasePath(str(raw.basePath, 'basePath', false) || '/'),
    auth: {
      mode,
      rolesClaim: str(auth.rolesClaim, 'auth.rolesClaim', false) || 'roles',
      roleMapping,
    },
    oidc,
  }
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>

/** Fetches and validates config.json. The cache is bypassed so a changed mount takes effect on reload. */
export async function loadConfig(fetchFn: FetchLike, url: string): Promise<AppConfig> {
  let res: Response
  try {
    res = await fetchFn(url, { cache: 'no-store', headers: { Accept: 'application/json' } })
  }
  catch {
    throw new ConfigError(`config.json could not be loaded from ${url}`)
  }
  if (!res.ok) throw new ConfigError(`config.json could not be loaded (HTTP ${res.status})`)
  let body: unknown
  try {
    body = await res.json()
  }
  catch {
    throw new ConfigError('config.json is not valid JSON')
  }
  return parseConfig(body)
}
