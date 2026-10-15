import { debounce } from './debounce'
import type { PoolFilter, Preview } from '~/types'

export interface LivePreviewOptions {
  run: (filter: PoolFilter, signal: AbortSignal) => Promise<Preview>
  onResult: (preview: Preview) => void
  onError: (error: Error | null) => void
  onPending: (pending: boolean) => void
  /** Quiet time after the last change before a request is sent. */
  delayMs?: number
}

export const PREVIEW_DELAY_MS = 500

/**
 * Debounced preview of an unsaved filter. A new request aborts the one in flight, and only the latest request may
 * report a result, so a slow stale answer can never overwrite a newer one.
 */
export function createLivePreview(options: LivePreviewOptions) {
  let controller: AbortController | undefined
  const send = async (filter: PoolFilter) => {
    controller?.abort()
    const mine = new AbortController()
    controller = mine
    options.onPending(true)
    options.onError(null)
    try {
      const result = await options.run(filter, mine.signal)
      if (controller === mine) options.onResult(result)
    }
    catch (e) {
      if (controller !== mine || mine.signal.aborted) return
      options.onError(e instanceof Error ? e : new Error(String(e)))
    }
    finally {
      if (controller === mine) options.onPending(false)
    }
  }
  const debounced = debounce((filter: PoolFilter) => void send(filter), options.delayMs ?? PREVIEW_DELAY_MS)

  return {
    /** Waits for the quiet time, then previews. */
    schedule: (filter: PoolFilter) => debounced(filter),
    /** Previews now (the refresh button). */
    now: (filter: PoolFilter) => {
      debounced.cancel()
      return send(filter)
    },
    /** Drops a pending call and aborts the request in flight (invalid form, unmount). */
    cancel: () => {
      debounced.cancel()
      controller?.abort()
      controller = undefined
      options.onPending(false)
    },
  }
}
