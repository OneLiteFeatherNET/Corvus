// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { useJobPolling } from './useJobPolling'
import type { Job } from '~/types'

const job = (status: string): Job => ({
  id: 'j1', type: 'train', snapshot_id: 's1', params: {}, status, runner: 'local', message: '',
  created_by: 'me', created_at: '2026-10-15T10:00:00Z', updated_at: '2026-10-15T10:00:00Z', finished_at: null,
})

function mountPolling(statuses: string[], initial: string) {
  const jobRef = ref<Job | null>(job(initial))
  const queue = [...statuses]
  const fetchJob = vi.fn(async () => job(queue.shift() ?? 'failed'))
  let api!: ReturnType<typeof useJobPolling>
  const wrapper = mount(defineComponent({
    setup() {
      api = useJobPolling(jobRef, fetchJob)
      return () => h('div')
    },
  }))
  return { jobRef, fetchJob, api, wrapper }
}

describe('useJobPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('refreshes a running job with backoff and stops at the final status', async () => {
    const { jobRef, fetchJob, api } = mountPolling(['running', 'succeeded'], 'running')
    api.sync()
    expect(api.poller.running).toBe(true)

    await vi.advanceTimersByTimeAsync(2000)
    expect(fetchJob).toHaveBeenCalledTimes(1)
    expect(jobRef.value?.status).toBe('running')

    await vi.advanceTimersByTimeAsync(3200)
    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(jobRef.value?.status).toBe('succeeded')
    expect(api.poller.running).toBe(false)

    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchJob).toHaveBeenCalledTimes(2)
  })

  it('does not poll a job that is already final', () => {
    const { api, fetchJob } = mountPolling([], 'succeeded')
    api.sync()
    expect(api.poller.running).toBe(false)
    expect(fetchJob).not.toHaveBeenCalled()
  })

  it('stops polling when the component unmounts', async () => {
    const { api, fetchJob, wrapper } = mountPolling(['running'], 'queued')
    api.sync()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchJob).not.toHaveBeenCalled()
  })

  it('keeps the last error and retries when a refresh fails', async () => {
    const jobRef = ref<Job | null>(job('running'))
    const fetchJob = vi.fn()
      .mockRejectedValueOnce(new Error('runner unreachable'))
      .mockResolvedValue(job('succeeded'))
    let api!: ReturnType<typeof useJobPolling>
    mount(defineComponent({
      setup() {
        api = useJobPolling(jobRef, fetchJob)
        return () => h('div')
      },
    }))
    api.sync()
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.lastError.value?.message).toBe('runner unreachable')
    await vi.advanceTimersByTimeAsync(3200)
    expect(api.lastError.value).toBeNull()
    expect(jobRef.value?.status).toBe('succeeded')
  })
})
