/** Reactive `window.matchMedia` result (the app is a client-only SPA, so `window` exists at setup). */
export function useMediaQuery(query: string) {
  const media = window.matchMedia(query)
  const matches = ref(media.matches)
  const update = () => { matches.value = media.matches }
  onMounted(() => media.addEventListener('change', update))
  onBeforeUnmount(() => media.removeEventListener('change', update))
  return matches
}
