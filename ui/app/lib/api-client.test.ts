import { describe, expect, it, vi } from 'vitest'
import { buildUrl, createApiClient } from './api-client'
import { ApiError, NetworkError } from './problem'

function setup(respond: () => Response | Promise<Response>, token: string | null = 'tok') {
  const fetchFn = vi.fn(async (..._args: [string, RequestInit?]) => respond())
  const onUnauthorized = vi.fn()
  const client = createApiClient({ baseUrl: 'https://api.test/api/v1', fetch: fetchFn, getToken: () => token, onUnauthorized })
  return { client, fetchFn, onUnauthorized }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const problem = (body: Record<string, unknown>, status: number) =>
  new Response(JSON.stringify({ type: 'about:blank', title: 'Problem', status, ...body }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  })

describe('buildUrl', () => {
  it('drops empty query values and encodes the rest', () => {
    expect(buildUrl('/api', '/x', { a: 'b c', e: '', n: undefined, z: 0, f: false })).toBe('/api/x?a=b+c&z=0&f=false')
  })

  it('has no question mark without parameters', () => {
    expect(buildUrl('/api', '/x')).toBe('/api/x')
  })
})

describe('request', () => {
  it('sends the bearer token and parses JSON', async () => {
    const { client, fetchFn } = setup(() => json({ items: [], next_cursor: null }))
    await expect(client.get('/pools', { query: { limit: 5 } })).resolves.toEqual({ items: [], next_cursor: null })
    const [url, init] = fetchFn.mock.calls[0]!
    expect(url).toBe('https://api.test/api/v1/pools?limit=5')
    expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  it('omits Authorization without a token', async () => {
    const { client, fetchFn } = setup(() => json({}), null)
    await client.get('/x')
    expect((fetchFn.mock.calls[0]![1]!.headers as Record<string, string>).Authorization).toBeUndefined()
  })

  it('sends a JSON body with its content type', async () => {
    const { client, fetchFn } = setup(() => json({ ok: true }))
    await client.post('/players/lookup', { body: { uuid: 'u' } })
    const init = fetchFn.mock.calls[0]![1]!
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{"uuid":"u"}')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('sends a raw CSV body unchanged', async () => {
    const { client, fetchFn } = setup(() => json({ imported: 1 }))
    await client.post('/labels/import', { rawBody: { content: 'a,b\n1,2\n', contentType: 'text/csv' } })
    const init = fetchFn.mock.calls[0]![1]!
    expect(init.body).toBe('a,b\n1,2\n')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('text/csv')
  })

  it('returns undefined for 204', async () => {
    const { client } = setup(() => new Response(null, { status: 204 }))
    await expect(client.delete('/pools/1')).resolves.toBeUndefined()
  })
})

describe('problem details', () => {
  it('turns an RFC 9457 body into an ApiError with the quality-gate reasons', async () => {
    const reasons = ['p99_latency_ms 12.0 is not below 10', 'recall_at_fpr_0_1pct below the incumbent']
    const { client } = setup(() => problem({ title: 'Conflict', detail: 'gate failed', reasons }, 409))
    const err = await client.post('/models/1/stage', { body: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
    expect((err as ApiError).reasons).toEqual(reasons)
    expect((err as ApiError).message).toBe('gate failed')
  })

  it('exposes validation errors', async () => {
    const { client } = setup(() => problem({ errors: [{ loc: ['body', 'name'], msg: 'Field required' }] }, 422))
    const err = (await client.post('/pools', { body: {} }).catch((e: unknown) => e)) as ApiError
    expect(err.errors[0]?.msg).toBe('Field required')
  })

  it('maps a non-problem error body to a generic problem', async () => {
    const { client } = setup(() => new Response('<html>bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }))
    const err = (await client.get('/x').catch((e: unknown) => e)) as ApiError
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(502)
    expect(err.problem.title).toBe('Bad Gateway')
  })

  it('reports network failures as NetworkError', async () => {
    const { client } = setup(() => Promise.reject(new TypeError('failed to fetch')))
    await expect(client.get('/x')).rejects.toBeInstanceOf(NetworkError)
  })
})

describe('401 handling', () => {
  it('calls onUnauthorized once and still rejects', async () => {
    const { client, onUnauthorized } = setup(() => problem({ title: 'Unauthorized' }, 401))
    await expect(client.get('/pools')).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('does not call onUnauthorized for 403', async () => {
    const { client, onUnauthorized } = setup(() => problem({ title: 'Forbidden' }, 403))
    await expect(client.get('/pools')).rejects.toMatchObject({ status: 403 })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})
