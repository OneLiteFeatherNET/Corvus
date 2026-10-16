import { describe, expect, it } from 'vitest'
import { createApiClient } from './api-client'
import {
  jobsResource, labelsResource, meResource, modelsResource, playersResource, poolsResource, scoresResource, snapshotsResource,
  summaryResource,
} from './resources'
import type { Model } from '~/types'

function harness(responder: (url: string, init: RequestInit) => unknown) {
  const calls: Array<{ url: string, init: RequestInit }> = []
  const client = createApiClient({
    baseUrl: '/api/v1',
    getToken: () => 't',
    onUnauthorized: () => {},
    fetch: async (url, init) => {
      calls.push({ url, init: init! })
      return new Response(JSON.stringify(responder(url, init!)), { status: 200 })
    },
  })
  return { client, calls }
}

const model = (name: string, version: string): Model => ({
  id: `${name}-${version}`, name, version, candidate_type: 'gbm', game_context: 'bw', domain: 'movement',
  snapshot_id: 's', metrics: {}, onnx_key: 'k', stage: 'registered', created_by: 'me', created_at: '2026-10-15T00:00:00Z',
})

describe('resources', () => {
  it('looks players up with POST and keeps the UUID out of the URL', async () => {
    const { client, calls } = harness(() => ({ pseudonym: 'p', key_id: 'k1', known: false }))
    const uuid = '069a79f4-44e9-4726-a5be-fca90e38aaf5'
    await playersResource(client).lookup(uuid)
    expect(calls[0]!.init.method).toBe('POST')
    expect(calls[0]!.url).toBe('/api/v1/players/lookup')
    expect(calls[0]!.url).not.toContain(uuid)
    expect(calls[0]!.init.body).toBe(JSON.stringify({ uuid }))
  })

  it('previews a saved pool with POST and no body', async () => {
    const { client, calls } = harness(() => ({}))
    await poolsResource(client).preview('abc')
    expect(calls[0]!.url).toBe('/api/v1/pools/abc/preview')
    expect(calls[0]!.init.method).toBe('POST')
    expect(calls[0]!.init.body).toBeUndefined()
  })

  it('passes list filters as query parameters and omits empty ones', async () => {
    const { client, calls } = harness(() => ({ items: [], next_cursor: null }))
    await jobsResource(client).list({ limit: 50, status: 'running', cursor: null })
    await jobsResource(client).list({ status: '' })
    await modelsResource(client).list({ stage: 'enforce', cursor: 'c' })
    await labelsResource(client).list({ subjectType: 'session', subject: 'ab' })
    expect(calls.map(c => c.url)).toEqual([
      '/api/v1/jobs?limit=50&status=running',
      '/api/v1/jobs',
      '/api/v1/models?cursor=c&stage=enforce',
      '/api/v1/labels?subject_type=session&subject=ab',
    ])
  })

  it('imports labels as text/csv', async () => {
    const { client, calls } = harness(() => ({ imported: 2 }))
    await labelsResource(client).importCsv('a\n1\n')
    expect((calls[0]!.init.headers as Record<string, string>)['Content-Type']).toBe('text/csv')
  })

  it('sends the stage and reason for a transition', async () => {
    const { client, calls } = harness(() => model('m', '1'))
    await modelsResource(client).setStage('id1', 'enforce', 'promote')
    expect(calls[0]!.url).toBe('/api/v1/models/id1/stage')
    expect(calls[0]!.init.body).toBe('{"stage":"enforce","reason":"promote"}')
  })
})

describe('endpoints of the extended API', () => {
  const pseudonym = 'ab'.repeat(32)

  it('reads the identity from /me and the dashboard from /summary', async () => {
    const { client, calls } = harness(() => ({}))
    await meResource(client).get()
    await summaryResource(client).get()
    expect(calls.map(c => c.url)).toEqual(['/api/v1/me', '/api/v1/summary'])
  })

  it('lists erasure requests from the API with paging and status filter', async () => {
    const { client, calls } = harness(() => ({ items: [], next_cursor: null }))
    await playersResource(client).erasureRequests({ limit: 50, cursor: 'c1', status: 'completed' })
    expect(calls[0]!.url).toBe('/api/v1/erasure-requests?limit=50&cursor=c1&status=completed')
    expect(calls[0]!.init.method).toBe('GET')
  })

  it('sends the model filters to the server and skips empty ones', async () => {
    const { client, calls } = harness(() => ({ items: [], next_cursor: null }))
    await modelsResource(client).list({ limit: 200, name: 'gbm', version: '', gameContext: 'bw', domain: 'movement', stage: 'canary' })
    expect(calls[0]!.url).toBe('/api/v1/models?limit=200&name=gbm&game_context=bw&domain=movement&stage=canary')
  })

  it('looks a model up by name and version and escapes both', async () => {
    const { client, calls } = harness(() => model('a/b', '1.0'))
    await modelsResource(client).byRef('a/b', '1.0 rc')
    expect(calls[0]!.url).toBe('/api/v1/models/a%2Fb/1.0%20rc')
  })

  it('previews an unsaved filter with the filter as body and the given abort signal', async () => {
    const { client, calls } = harness(() => ({}))
    const controller = new AbortController()
    await poolsResource(client).previewFilter({ game_contexts: ['bw'] } as never, controller.signal)
    expect(calls[0]!.url).toBe('/api/v1/pools/preview')
    expect(calls[0]!.init.method).toBe('POST')
    expect(calls[0]!.init.body).toBe('{"game_contexts":["bw"]}')
    expect(calls[0]!.init.signal).toBe(controller.signal)
  })

  it('reads the latest scores and the history of a pseudonym', async () => {
    const { client, calls } = harness(() => ({ items: [] }))
    await scoresResource(client).latest(pseudonym)
    await scoresResource(client).history(pseudonym, { gameContext: 'bw', limit: 250 })
    expect(calls[0]!.url).toBe(`/api/v1/scores/players/${pseudonym}`)
    expect(calls[1]!.url).toBe(`/api/v1/scores/players/${pseudonym}/history?game_context=bw&limit=250`)
  })
})

describe('snapshot sessions', () => {
  it('sends the cursor, the limit and exclude_erased, and omits an absent cursor', async () => {
    const { client, calls } = harness(() => ({ items: [], next_cursor: null }))
    const snapshots = snapshotsResource(client)
    await snapshots.sessions('s1', { limit: 50, cursor: 'abc', excludeErased: true })
    await snapshots.sessions('s1', { limit: 50, cursor: null })
    expect(calls[0]!.url).toBe('/api/v1/snapshots/s1/sessions?limit=50&cursor=abc&exclude_erased=true')
    expect(calls[1]!.url).toBe('/api/v1/snapshots/s1/sessions?limit=50')
  })
})
