import { describe, expect, it, vi } from 'vitest'
import { useAction } from './useAction'
import { usePagedList } from './usePagedList'
import { ApiError } from '~/lib/problem'
import type { Paged } from '~/types'

const page = (items: number[], next: string | null): Paged<number> => ({ items, next_cursor: next })

describe('usePagedList', () => {
  it('loads the first page, then appends the next with the cursor', async () => {
    const fetchPage = vi.fn(async (cursor: string | null) => (cursor ? page([3, 4], null) : page([1, 2], 'c1')))
    const list = usePagedList(fetchPage)
    await list.load()
    expect(list.items.value).toEqual([1, 2])
    expect(list.hasMore()).toBe(true)
    await list.loadMore()
    expect(fetchPage).toHaveBeenLastCalledWith('c1')
    expect(list.items.value).toEqual([1, 2, 3, 4])
    expect(list.hasMore()).toBe(false)
  })

  it('does nothing on loadMore without a cursor', async () => {
    const fetchPage = vi.fn(async () => page([1], null))
    const list = usePagedList(fetchPage)
    await list.load()
    await list.loadMore()
    expect(fetchPage).toHaveBeenCalledTimes(1)
  })

  it('keeps items and exposes the problem when a later page fails', async () => {
    const err = new ApiError({ type: 'about:blank', title: 'Boom', status: 500 })
    const fetchPage = vi.fn()
      .mockResolvedValueOnce(page([1], 'c'))
      .mockRejectedValueOnce(err)
    const list = usePagedList(fetchPage)
    await list.load()
    await list.loadMore()
    expect(list.items.value).toEqual([1])
    expect(list.error.value).toBe(err)
    expect(list.loading.value).toBe(false)
  })

  it('ignores a stale response when a newer load started', async () => {
    let release!: (p: Paged<number>) => void
    const slow = new Promise<Paged<number>>((r) => {
      release = r
    })
    const fetchPage = vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(page([9], null))
    const list = usePagedList(fetchPage)
    const first = list.load()
    await list.load()
    release(page([1], null))
    await first
    expect(list.items.value).toEqual([9])
  })
})

describe('useAction', () => {
  it('tracks pending and returns the result', async () => {
    const a = useAction(async (x: number) => x * 2)
    const p = a.run(4)
    expect(a.pending.value).toBe(true)
    expect(await p).toBe(8)
    expect(a.pending.value).toBe(false)
  })

  it('stores the error instead of throwing and clears it on the next run', async () => {
    const fn = vi.fn().mockRejectedValueOnce(new Error('nope')).mockResolvedValueOnce('ok')
    const a = useAction(fn)
    expect(await a.run()).toBeUndefined()
    expect(a.error.value?.message).toBe('nope')
    expect(await a.run()).toBe('ok')
    expect(a.error.value).toBeNull()
  })

  it('ignores a second call while one is running', async () => {
    let release!: () => void
    const fn = vi.fn(() => new Promise<void>((r) => {
      release = r
    }))
    const a = useAction(fn)
    const first = a.run()
    await a.run()
    release()
    await first
    expect(fn).toHaveBeenCalledTimes(1)
  })
})
