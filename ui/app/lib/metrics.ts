export interface MetricDef {
  /** Key in the model's metrics JSON. */
  key: string
  /** i18n key suffix under models.metrics. */
  label: string
  /** Which direction is better, used to highlight the winner when comparing. */
  better: 'higher' | 'lower'
  format: 'ratio' | 'ms' | 'bytes'
}

export const METRICS: readonly MetricDef[] = [
  { key: 'recall_at_fpr_0_1pct', label: 'recallAtFpr', better: 'higher', format: 'ratio' },
  { key: 'pr_auc', label: 'prAuc', better: 'higher', format: 'ratio' },
  { key: 'roc_auc', label: 'rocAuc', better: 'higher', format: 'ratio' },
  { key: 'ece', label: 'ece', better: 'lower', format: 'ratio' },
  { key: 'p50_latency_ms', label: 'p50', better: 'lower', format: 'ms' },
  { key: 'p99_latency_ms', label: 'p99', better: 'lower', format: 'ms' },
  { key: 'rss_bytes', label: 'rss', better: 'lower', format: 'bytes' },
]

export function metricValue(metrics: Record<string, unknown>, key: string): number | null {
  const v = metrics[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function formatMetric(value: number | null, format: MetricDef['format']): string {
  if (value === null) return '–'
  switch (format) {
    case 'ms': return `${value.toFixed(2)} ms`
    case 'bytes': return `${(value / (1024 * 1024)).toFixed(1)} MiB`
    default: return value.toFixed(4)
  }
}

export type Winner = 'a' | 'b' | 'tie' | 'none'

/** Which of two values is better for a metric; `none` when either is missing. */
export function compareMetric(def: MetricDef, a: number | null, b: number | null): Winner {
  if (a === null || b === null) return 'none'
  if (a === b) return 'tie'
  const aBetter = def.better === 'higher' ? a > b : a < b
  return aBetter ? 'a' : 'b'
}

export function compareModels(a: Record<string, unknown>, b: Record<string, unknown>) {
  return METRICS.map((def) => {
    const av = metricValue(a, def.key)
    const bv = metricValue(b, def.key)
    return { def, a: av, b: bv, winner: compareMetric(def, av, bv) }
  })
}

/** Stages a model may move to from `stage`, as the API allows them. */
export function nextStages(stage: string): string[] {
  const order = ['registered', 'shadow', 'canary', 'enforce']
  if (stage === 'archived') return []
  const i = order.indexOf(stage)
  const forward = i >= 0 && i < order.length - 1 ? [order[i + 1]!] : []
  return [...forward, 'archived']
}
