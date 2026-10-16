import { enumLabel } from '~/lib/enums'

/** Translates an API enum value; unknown values are shown humanised, never as a dotted key. */
export function useEnumLabel() {
  const { t, te } = useI18n()
  return (prefix: string, value: string) => enumLabel(prefix, value, k => t(k), k => te(k))
}
