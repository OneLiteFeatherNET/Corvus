import { ref } from 'vue'

/** Wraps a mutating call with pending and error state. Errors are kept in `error` for display; `run` then resolves to undefined. */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const pending = ref(false)
  const error = ref<Error | null>(null)

  async function run(...args: A): Promise<R | undefined> {
    if (pending.value) return undefined
    pending.value = true
    error.value = null
    try {
      return await fn(...args)
    }
    catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e))
      return undefined
    }
    finally {
      pending.value = false
    }
  }

  return { run, pending, error, reset: () => (error.value = null) }
}
