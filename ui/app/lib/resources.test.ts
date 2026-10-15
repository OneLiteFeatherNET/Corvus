import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from './api-client'
import { findModel, jobsResource, labelsResource, modelsResource, playersResource, poolsResource } from './resources'
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

describe('findModel', () => {
  it('walks the pages until it finds name and version', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [model('a', '1')], next_cursor: 'c1' })
      .mockResolvedValueOnce({ items: [model('a', '2'), model('b', '1')], next_cursor: null })
    const found = await findModel({ list } as never, 'b', '1')
    expect(found?.id).toBe('b-1')
    expect(list).toHaveBeenLastCalledWith({ limit: 200, cursor: 'c1' })
  })

  it('returns null when the registry has no such model', async () => {
    const list = vi.fn().mockResolvedValue({ items: [model('a', '1')], next_cursor: null })
    expect(await findModel({ list } as never, 'x', '9')).toBeNull()
  })

  it('stops after maxPages', async () => {
    const list = vi.fn().mockResolvedValue({ items: [], next_cursor: 'more' })
    expect(await findModel({ list } as never, 'x', '9', 3)).toBeNull()
    expect(list).toHaveBeenCalledTimes(3)
  })
})
