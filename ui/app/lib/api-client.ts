import type { FetchLike } from './config'
import { NetworkError, problemFromResponse } from './problem'

export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  query?: Record<string, QueryValue>
  /** JSON body. */
  body?: unknown
  /** Raw body with its own content type (CSV import). */
  rawBody?: { content: string, contentType: string }
  signal?: AbortSignal
}

export interface ApiClientOptions {
  baseUrl: string
  fetch: FetchLike
  getToken: () => string | null | Promise<string | null>
  /** Called once per 401 response; the client still rejects so callers stop their work. */
  onUnauthorized: () => void
}

export interface ApiClient {
  get: <T>(path: string, opts?: RequestOptions) => Promise<T>
  post: <T>(path: string, opts?: RequestOptions) => Promise<T>
  put: <T>(path: string, opts?: RequestOptions) => Promise<T>
  delete: <T = void>(path: string, opts?: RequestOptions) => Promise<T>
}

export function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return `${baseUrl}${path}${qs ? `?${qs}` : ''}`
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json, application/problem+json' }
    const token = await options.getToken()
    if (token) headers.Authorization = `Bearer ${token}`

    let body: string | undefined
    if (opts.rawBody) {
      headers['Content-Type'] = opts.rawBody.contentType
      body = opts.rawBody.content
    }
    else if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(opts.body)
    }

    let res: Response
    try {
      res = await options.fetch(buildUrl(options.baseUrl, path, opts.query), {
        method,
        headers,
        body,
        signal: opts.signal,
        // Responses can carry personal-data adjacent identifiers; never reuse them from a shared cache.
        cache: 'no-store',
      })
    }
    catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') throw e
      throw new NetworkError()
    }

    if (res.status === 401) options.onUnauthorized()
    if (!res.ok) throw await problemFromResponse(res)
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  return {
    get: (path, opts) => request('GET', path, opts),
    post: (path, opts) => request('POST', path, opts),
    put: (path, opts) => request('PUT', path, opts),
    delete: (path, opts) => request('DELETE', path, opts),
  }
}
