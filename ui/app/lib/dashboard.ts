import type { Summary } from '~/types'

export const STAGES = ['registered', 'shadow', 'canary', 'enforce', 'archived'] as const

/** Per stage count including stages the summary leaves out. */
export function stageCounts(summary: Pick<Summary, 'models_by_stage'>): Record<string, number> {
  return Object.fromEntries(STAGES.map(s => [s, summary.models_by_stage[s] ?? 0]))
}

export function modelTotal(summary: Pick<Summary, 'models_by_stage'>): number {
  return Object.values(summary.models_by_stage).reduce((a, b) => a + b, 0)
}

export function runningJobs(summary: Pick<Summary, 'jobs_by_status'>): number {
  return (summary.jobs_by_status.running ?? 0) + (summary.jobs_by_status.queued ?? 0)
}
