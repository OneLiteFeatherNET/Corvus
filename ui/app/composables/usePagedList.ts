import { ref, shallowRef } from 'vue'
import { ApiError } from '~/lib/problem'
import type { Paged } from '~/types'

/** Keyset-paginated list: `load` replaces the items, `loadMore` appends the next page. */
export function usePagedList<T>(fetchPage: (cursor: string | null) => Promise<Paged<T>>) {
  const items = shallowRef<T[]>([])
  const nextCursor = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<Error | null>(null)
  const loaded = ref(false)
  let generation = 0

  async function run(cursor: string | null, append: boolean) {
    const mine = ++generation
    loading.value = true
    error.value = null
    try {
      const page = await fetchPage(cursor)
      if (mine !== generation) return
      items.value = append ? [...items.value, ...page.items] : page.items
      nextCursor.value = page.next_cursor
      loaded.value = true
    }
    catch (e) {
      if (mine !== generation) return
      error.value = e instanceof Error ? e : new Error(String(e))
      if (!(e instanceof ApiError) && !append) items.value = []
    }
    finally {
      if (mine === generation) loading.value = false
    }
  }

  return {
    items,
    loading,
    error,
    loaded,
    hasMore: () => nextCursor.value !== null,
    nextCursor,
    load: () => run(null, false),
    loadMore: () => (nextCursor.value ? run(nextCursor.value, true) : Promise.resolve()),
  }
}
