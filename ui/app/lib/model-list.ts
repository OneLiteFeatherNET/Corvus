import type { Model } from '~/types'

/** `name@version`, the compact reference used in the compare URL. */
export function modelRef(m: Pick<Model, 'name' | 'version'>): string {
  return `${m.name}@${m.version}`
}

export function parseModelRef(ref: string): { name: string, version: string } | null {
  const at = ref.lastIndexOf('@')
  if (at <= 0 || at === ref.length - 1) return null
  return { name: ref.slice(0, at), version: ref.slice(at + 1) }
}

export function compareQuery(a: Pick<Model, 'name' | 'version'>, b: Pick<Model, 'name' | 'version'>) {
  return { a: modelRef(a), b: modelRef(b) }
}
