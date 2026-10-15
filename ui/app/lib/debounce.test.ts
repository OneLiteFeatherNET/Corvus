import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { debounce } from './debounce'

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('calls once with the last arguments after the quiet time', () => {
    const fn = vi.fn()
    const d = debounce(fn, 300)
    d('a')
    vi.advanceTimersByTime(200)
    d('b')
    vi.advanceTimersByTime(299)
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fn).toHaveBeenCalledOnce()
    expect(fn).toHaveBeenCalledWith('b')
  })

  it('drops a pending call on cancel', () => {
    const fn = vi.fn()
    const d = debounce(fn, 300)
    d()
    d.cancel()
    vi.advanceTimersByTime(1000)
    expect(fn).not.toHaveBeenCalled()
  })

  it('runs a pending call at once on flush and does nothing without one', () => {
    const fn = vi.fn()
    const d = debounce(fn, 300)
    d.flush()
    expect(fn).not.toHaveBeenCalled()
    d('x')
    d.flush()
    expect(fn).toHaveBeenCalledWith('x')
    vi.advanceTimersByTime(1000)
    expect(fn).toHaveBeenCalledOnce()
  })
})
