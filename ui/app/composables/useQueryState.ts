/**
 * Binds a string filter to a URL query parameter, so a view can be shared and every browser tab keeps its own
 * state. An empty value removes the parameter.
 */
export function useQueryState(key: string, fallback = '') {
  const route = useRoute()
  const router = useRouter()
  return computed<string>({
    get: () => {
      const v = route.query[key]
      return (Array.isArray(v) ? v[0] : v) ?? fallback
    },
    set: (value) => {
      const { [key]: _old, ...rest } = route.query
      void router.replace({ query: !value || value === fallback ? rest : { ...rest, [key]: value } })
    },
  })
}
