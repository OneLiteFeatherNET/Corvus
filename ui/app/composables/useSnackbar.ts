const DURATION_MS = 5000
let timer: ReturnType<typeof setTimeout> | undefined

/** App-wide transient message (one at a time). The M3Snackbar in the layouts shows it. */
export function useSnackbar() {
  const message = useState<string>('corvus.snackbar', () => '')

  function dismiss() {
    clearTimeout(timer)
    message.value = ''
  }
  function show(text: string) {
    clearTimeout(timer)
    message.value = text
    timer = setTimeout(dismiss, DURATION_MS)
  }
  return { message, show, dismiss }
}
