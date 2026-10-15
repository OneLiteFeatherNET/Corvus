import type { Model } from '~/types'

export interface ModelFilters {
  name: string
  gameContext: string
  domain: string
  stage: string
}

/** The API filters by stage only; name, game context and domain are filtered here. */
export function filterModels(models: Model[], f: ModelFilters): Model[] {
  const name = f.name.trim().toLowerCase()
  return models.filter(m =>
    (!name || m.name.toLowerCase().includes(name))
    && (!f.gameContext || m.game_context === f.gameContext)
    && (!f.domain || m.domain === f.domain)
    && (!f.stage || m.stage === f.stage),
  )
}

export function distinctValues(models: Model[], key: 'game_context' | 'domain'): string[] {
  return [...new Set(models.map(m => m[key]))].sort()
}

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
