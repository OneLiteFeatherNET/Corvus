import type { Job, Model, Paged } from '~/types'

export const STAGES = ['registered', 'shadow', 'canary', 'enforce', 'archived'] as const

export interface Count {
  value: number
  /** More pages exist, so `value` is a lower bound. */
  partial: boolean
}

/** The API has no aggregate endpoint; counts come from the first page (size 200). */
export function countOf<T>(page: Paged<T>): Count {
  return { value: page.items.length, partial: page.next_cursor !== null }
}

export function modelsPerStage(models: Pick<Model, 'stage'>[]): Record<string, number> {
  const out: Record<string, number> = Object.fromEntries(STAGES.map(s => [s, 0]))
  for (const m of models) out[m.stage] = (out[m.stage] ?? 0) + 1
  return out
}

export function runningJobs(jobs: Pick<Job, 'status'>[]): number {
  return jobs.filter(j => j.status === 'running' || j.status === 'queued').length
}

/** List responses are newest first already; this keeps the contract explicit and bounded. */
export function recent<T>(items: T[], n = 5): T[] {
  return items.slice(0, n)
}
