import { onScopeDispose, ref } from 'vue'
import type { Ref } from 'vue'
import { createPoller, isFinalJobState } from '~/lib/polling'
import type { BackoffOptions } from '~/lib/polling'
import type { Job } from '~/types'

export interface JobPollingOptions {
  backoff?: BackoffOptions
  schedule?: (fn: () => void, ms: number) => unknown
  cancel?: (handle: unknown) => void
}

/**
 * Refreshes a job while it is queued or running, with growing delays. `GET /jobs/{id}` makes the API ask the
 * runner, so polling is what moves a job to its final status.
 */
export function useJobPolling(job: Ref<Job | null>, fetchJob: (id: string) => Promise<Job>, o: JobPollingOptions = {}) {
  const lastError = ref<Error | null>(null)

  const poller = createPoller({
    backoff: o.backoff,
    schedule: o.schedule ?? ((fn, ms) => setTimeout(fn, ms)),
    cancel: o.cancel ?? (h => clearTimeout(h as ReturnType<typeof setTimeout>)),
    tick: async () => {
      const current = job.value
      if (!current) return false
      try {
        job.value = await fetchJob(current.id)
        lastError.value = null
      }
      catch (e) {
        lastError.value = e instanceof Error ? e : new Error(String(e))
        return true
      }
      return !isFinalJobState(job.value.status)
    },
  })

  /** Starts polling when the job is not final yet. */
  function sync() {
    poller.stop()
    if (job.value && !isFinalJobState(job.value.status)) poller.start()
  }

  onScopeDispose(() => poller.stop())

  return { sync, stop: () => poller.stop(), lastError, poller }
}
