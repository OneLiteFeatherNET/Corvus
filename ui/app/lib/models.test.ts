import { describe, expect, it } from 'vitest'
import { modelTotal, runningJobs, stageCounts } from './dashboard'
import { formatMetric, METRICS, compareMetric, compareModels, metricValue, nextStages } from './metrics'
import { compareQuery, modelRef, parseModelRef } from './model-list'
import { parseJobParams } from './job-params'
import { readBasePath } from './base'

describe('stage transitions offered by the UI', () => {
  it('offers the next stage and archive', () => {
    expect(nextStages('registered')).toEqual(['shadow', 'archived'])
    expect(nextStages('canary')).toEqual(['enforce', 'archived'])
    expect(nextStages('enforce')).toEqual(['archived'])
  })

  it('offers nothing from archived', () => {
    expect(nextStages('archived')).toEqual([])
  })
})

describe('metrics', () => {
  it('reads only finite numbers', () => {
    expect(metricValue({ a: 1.5, b: 'x', c: Number.NaN }, 'a')).toBe(1.5)
    expect(metricValue({ b: 'x' }, 'b')).toBeNull()
    expect(metricValue({}, 'z')).toBeNull()
  })

  it('formats by unit', () => {
    expect(formatMetric(null, 'ratio')).toBe('–')
    expect(formatMetric(1.234, 'ms')).toBe('1.23 ms')
    expect(formatMetric(3 * 1024 * 1024, 'bytes')).toBe('3.0 MiB')
    expect(formatMetric(0.91234, 'ratio')).toBe('0.9123')
  })

  it('declares the metrics the registry shows', () => {
    expect(METRICS.map(d => d.key)).toEqual([
      'recall_at_fpr_0_1pct', 'pr_auc', 'roc_auc', 'ece', 'p50_latency_ms', 'p99_latency_ms', 'rss_bytes',
    ])
  })

  it('picks the winner by direction and ignores missing values', () => {
    const higher = METRICS.find(d => d.key === 'pr_auc')!
    const lower = METRICS.find(d => d.key === 'p99_latency_ms')!
    expect(compareMetric(higher, 0.9, 0.8)).toBe('a')
    expect(compareMetric(lower, 9, 7)).toBe('b')
    expect(compareMetric(lower, 7, 7)).toBe('tie')
    expect(compareMetric(lower, null, 7)).toBe('none')
  })

  it('compares two metric sets row by row', () => {
    const rows = compareModels({ pr_auc: 0.9, ece: 0.1 }, { pr_auc: 0.8, ece: 0.05 })
    expect(rows.find(r => r.def.key === 'pr_auc')!.winner).toBe('a')
    expect(rows.find(r => r.def.key === 'ece')!.winner).toBe('b')
    expect(rows.find(r => r.def.key === 'roc_auc')!.winner).toBe('none')
  })
})

describe('model list helpers', () => {
  it('round-trips a name@version reference, including names with @', () => {
    expect(modelRef({ name: 'a', version: '1.0' })).toBe('a@1.0')
    expect(parseModelRef('a@b@1.0')).toEqual({ name: 'a@b', version: '1.0' })
    expect(parseModelRef('noversion')).toBeNull()
    expect(parseModelRef('@1')).toBeNull()
    expect(compareQuery({ name: 'a', version: '1' }, { name: 'b', version: '2' })).toEqual({ a: 'a@1', b: 'b@2' })
  })
})

describe('dashboard helpers', () => {
  const summary = {
    models_by_stage: { enforce: 2, shadow: 1 },
    jobs_by_status: { queued: 1, running: 2, succeeded: 5, failed: 1 },
  }

  it('counts per stage including stages the summary leaves out', () => {
    expect(stageCounts(summary)).toEqual({ registered: 0, shadow: 1, canary: 0, enforce: 2, archived: 0 })
  })

  it('totals the models over all stages', () => {
    expect(modelTotal(summary)).toBe(3)
  })

  it('counts queued and running jobs', () => {
    expect(runningJobs(summary)).toBe(3)
    expect(runningJobs({ jobs_by_status: {} })).toBe(0)
  })
})

describe('parseJobParams', () => {
  it('treats empty input as no parameters', () => {
    expect(parseJobParams('  ')).toEqual({ ok: true, value: {} })
  })

  it('accepts an object and rejects other JSON', () => {
    expect(parseJobParams('{"epochs": 3}')).toEqual({ ok: true, value: { epochs: 3 } })
    expect(parseJobParams('[1]')).toEqual({ ok: false, error: 'notObject' })
    expect(parseJobParams('{oops')).toEqual({ ok: false, error: 'invalidJson' })
    expect(parseJobParams(JSON.stringify({ x: 'y'.repeat(17_000) }))).toEqual({ ok: false, error: 'tooLarge' })
  })
})

describe('readBasePath', () => {
  const doc = (href: string | null) => ({ querySelector: () => (href === null ? null : { getAttribute: () => href }) }) as never

  it('is the root without a base tag', () => {
    expect(readBasePath(doc(null))).toBe('/')
    expect(readBasePath(null)).toBe('/')
  })

  it('reads the injected base path and normalizes the trailing slash', () => {
    expect(readBasePath(doc('/corvus/'))).toBe('/corvus/')
    expect(readBasePath(doc('/corvus'))).toBe('/corvus/')
  })
})
