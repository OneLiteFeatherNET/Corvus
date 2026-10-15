import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from './api-client'
import type { Session } from './auth'
import { withServerIdentity } from './identity'

const claimSession: Session = { token: 't', roles: ['viewer'], subject: 'claim-subject' }

function clientWith(respond: () => Response | Promise<Response>) {
  const fetch = vi.fn(async () => respond())
  const onUnauthorized = vi.fn()
  const client = createApiClient({ baseUrl: '/api/v1', fetch, getToken: () => 't', onUnauthorized })
  return { client, fetch }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

describe('withServerIdentity', () => {
  it('takes roles and display name from /me instead of the token claims', async () => {
    const { client, fetch } = clientWith(() => json({ subject: 'sub-1', display_name: 'Zoe', roles: ['staff', 'admin'] }))
    const session = await withServerIdentity(client, claimSession)
    expect(fetch).toHaveBeenCalledWith('/api/v1/me', expect.anything())
    expect(session).toEqual({ token: 't', roles: ['staff', 'admin'], subject: 'Zoe' })
  })

  it('falls back to the subject when the token has no display name', async () => {
    const { client } = clientWith(() => json({ subject: 'sub-1', display_name: null, roles: ['viewer'] }))
    expect((await withServerIdentity(client, claimSession)).subject).toBe('sub-1')
  })

  it('ignores roles this UI does not know', async () => {
    const { client } = clientWith(() => json({ subject: 's', display_name: null, roles: ['admin', 'wizard'] }))
    expect((await withServerIdentity(client, claimSession)).roles).toEqual(['admin'])
  })

  it('keeps the token-claim roles when /me answers with an error', async () => {
    const { client } = clientWith(() => json({ title: 'Not Found', status: 404 }, 404))
    expect(await withServerIdentity(client, claimSession)).toEqual(claimSession)
  })

  it('keeps the token-claim roles when /me cannot be reached', async () => {
    const { client } = clientWith(() => Promise.reject(new TypeError('network down')))
    expect(await withServerIdentity(client, claimSession)).toEqual(claimSession)
  })
})
