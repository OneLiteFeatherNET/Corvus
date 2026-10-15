import { describe, expect, it, vi } from 'vitest'
import { backoffDelay, createPoller, DEFAULT_BACKOFF, isFinalJobState } from './polling'

describe('backoffDelay', () => {
  it('grows exponentially and is capped', () => {
    const delays = [0, 1, 2, 3, 4, 5, 10, 50].map(n => backoffDelay(n))
    expect(delays[0]).toBe(DEFAULT_BACKOFF.initialMs)
    expect(delays[1]).toBeGreaterThan(delays[0]!)
    expect(delays[2]).toBeGreaterThan(delays[1]!)
    expect(delays.at(-1)).toBe(DEFAULT_BACKOFF.maxMs)
    expect(Math.max(...delays)).toBe(DEFAULT_BACKOFF.maxMs)
  })

  it('treats negative attempts as the first', () => {
    expect(backoffDelay(-3)).toBe(DEFAULT_BACKOFF.initialMs)
  })
})

describe('isFinalJobState', () => {
  it('knows the terminal states', () => {
    expect(isFinalJobState('succeeded')).toBe(true)
    expect(isFinalJobState('failed')).toBe(true)
    expect(isFinalJobState('running')).toBe(false)
    expect(isFinalJobState('queued')).toBe(false)
  })
})

describe('createPoller', () => {
  function harness(results: Array<boolean | Error>) {
    const scheduled: Array<{ fn: () => void, ms: number }> = []
    const queue = [...results]
    const tick = vi.fn(async () => {
      const r = queue.shift() ?? false
      if (r instanceof Error) throw r
      return r
    })
    const poller = createPoller({
      tick,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      cancel: vi.fn(),
    })
    // Runs the oldest scheduled callback and lets its promise chain settle.
    const fire = async () => {
      scheduled.shift()!.fn()
      await vi.waitFor(() => expect(tick).toHaveBeenCalled())
      await Promise.resolve()
      await Promise.resolve()
    }
    return { poller, scheduled, tick, fire }
  }

  it('polls with growing delays until tick says stop', async () => {
    const h = harness([true, true, false])
    h.poller.start()
    expect(h.scheduled.map(s => s.ms)).toEqual([2000])
    await h.fire()
    expect(h.scheduled.map(s => s.ms)).toEqual([3200])
    await h.fire()
    expect(h.scheduled).toHaveLength(1)
    await h.fire()
    expect(h.scheduled).toHaveLength(0)
    expect(h.poller.running).toBe(false)
    expect(h.tick).toHaveBeenCalledTimes(3)
  })

  it('keeps polling after a failed refresh', async () => {
    const h = harness([new Error('runner down'), false])
    h.poller.start()
    await h.fire()
    expect(h.scheduled).toHaveLength(1)
    expect(h.poller.running).toBe(true)
  })

  it('stop cancels the pending timer and ignores a late tick', async () => {
    const h = harness([true])
    h.poller.start()
    const pending = h.scheduled[0]!
    h.poller.stop()
    expect(h.poller.running).toBe(false)
    pending.fn()
    await Promise.resolve()
    expect(h.tick).not.toHaveBeenCalled()
  })

  it('does not start twice', () => {
    const h = harness([])
    h.poller.start()
    h.poller.start()
    expect(h.scheduled).toHaveLength(1)
  })
})
