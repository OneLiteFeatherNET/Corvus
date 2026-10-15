export interface BackoffOptions {
  initialMs: number
  factor: number
  maxMs: number
}

export const DEFAULT_BACKOFF: BackoffOptions = { initialMs: 2000, factor: 1.6, maxMs: 30_000 }

/** Delay before poll number `attempt` (0-based): exponential, capped. */
export function backoffDelay(attempt: number, o: BackoffOptions = DEFAULT_BACKOFF): number {
  return Math.min(o.maxMs, Math.round(o.initialMs * o.factor ** Math.max(0, attempt)))
}

export const FINAL_JOB_STATES: readonly string[] = ['succeeded', 'failed']

export function isFinalJobState(status: string): boolean {
  return FINAL_JOB_STATES.includes(status)
}

export interface PollerDeps {
  /** Runs one refresh and returns true when polling should continue. */
  tick: () => Promise<boolean>
  schedule: (fn: () => void, ms: number) => unknown
  cancel: (handle: unknown) => void
  backoff?: BackoffOptions
}

export interface Poller {
  start: () => void
  stop: () => void
  readonly running: boolean
}

/** Polls with growing delays until `tick` returns false or `stop` is called. Timers are injected for tests. */
export function createPoller(deps: PollerDeps): Poller {
  let handle: unknown = null
  let attempt = 0
  let running = false

  const next = () => {
    handle = deps.schedule(async () => {
      if (!running) return
      let again: boolean
      try {
        again = await deps.tick()
      }
      catch {
        again = true
      }
      if (!running) return
      if (again) {
        attempt++
        next()
      }
      else running = false
    }, backoffDelay(attempt, deps.backoff))
  }

  return {
    start() {
      if (running) return
      running = true
      attempt = 0
      next()
    },
    stop() {
      running = false
      if (handle !== null) deps.cancel(handle)
      handle = null
    },
    get running() {
      return running
    },
  }
}
