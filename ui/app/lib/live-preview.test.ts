import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createLivePreview, PREVIEW_DELAY_MS } from './live-preview'
import type { PoolFilter, Preview } from '~/types'

const filter = (ctx: string): PoolFilter => ({ game_contexts: [ctx] }) as PoolFilter
const preview = (sessions: number): Preview => ({ sessions }) as Preview

interface Pending {
  filter: PoolFilter
  signal: AbortSignal
  resolve: (p: Preview) => void
  reject: (e: unknown) => void
}

function setup() {
  const requests: Pending[] = []
  const results: Preview[] = []
  const errors: Array<Error | null> = []
  const pending: boolean[] = []
  const run = vi.fn((f: PoolFilter, signal: AbortSignal) =>
    new Promise<Preview>((resolve, reject) => {
      requests.push({ filter: f, signal, resolve, reject })
    }))
  const lp = createLivePreview({
    run,
    onResult: p => results.push(p),
    onError: e => errors.push(e),
    onPending: p => pending.push(p),
  })
  return { lp, run, requests, results, errors, pending }
}

describe('live preview', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('sends one request after the quiet time, not one per change', async () => {
    const { lp, run, requests } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS - 1)
    lp.schedule(filter('ab'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS - 1)
    expect(run).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(run).toHaveBeenCalledTimes(1)
    expect(requests[0]!.filter).toEqual(filter('ab'))
  })

  it('aborts the request in flight when a newer one starts', async () => {
    const { lp, requests } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    expect(requests[0]!.signal.aborted).toBe(false)
    lp.schedule(filter('b'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    expect(requests[0]!.signal.aborted).toBe(true)
    expect(requests[1]!.signal.aborted).toBe(false)
  })

  it('never lets a stale answer overwrite a newer one', async () => {
    const { lp, requests, results } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    lp.schedule(filter('b'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    requests[1]!.resolve(preview(2))
    await vi.advanceTimersByTimeAsync(0)
    requests[0]!.resolve(preview(1))
    await vi.advanceTimersByTimeAsync(0)
    expect(results.map(r => r.sessions)).toEqual([2])
  })

  it('does not report an abort as an error', async () => {
    const { lp, requests, errors } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    lp.schedule(filter('b'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    requests[0]!.reject(new DOMException('aborted', 'AbortError'))
    await vi.advanceTimersByTimeAsync(0)
    expect(errors.filter(e => e !== null)).toEqual([])
  })

  it('reports a failure of the latest request', async () => {
    const { lp, requests, errors, pending } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    requests[0]!.reject(new Error('boom'))
    await vi.advanceTimersByTimeAsync(0)
    expect(errors.at(-1)?.message).toBe('boom')
    expect(pending.at(-1)).toBe(false)
  })

  it('previews immediately and drops the pending debounced call', async () => {
    const { lp, run } = setup()
    lp.schedule(filter('a'))
    void lp.now(filter('b'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS * 2)
    expect(run).toHaveBeenCalledTimes(1)
    expect(run.mock.calls[0]![0]).toEqual(filter('b'))
  })

  it('cancels the pending call and the request in flight', async () => {
    const { lp, run, requests, pending } = setup()
    lp.schedule(filter('a'))
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS)
    lp.schedule(filter('b'))
    lp.cancel()
    await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS * 2)
    expect(run).toHaveBeenCalledTimes(1)
    expect(requests[0]!.signal.aborted).toBe(true)
    expect(pending.at(-1)).toBe(false)
  })
})
