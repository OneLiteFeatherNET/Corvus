import { describe, expect, it } from 'vitest'
import { buildSeries, DEFAULT_GEOMETRY, groupByContext, plotSeries, seriesKey } from './score-view'
import type { ScoreHistory, ScoreLatest } from '~/types'

const latest = (over: Partial<ScoreLatest>): ScoreLatest => ({
  domain: 'movement', game_context: 'bw', model_name: 'gbm', model_version: '1', stage: 'enforce', score: 0.5,
  smoothed: 0.4, windows: 3, updated_at: '2026-10-15T10:00:00Z', ...over,
})

const hist = (over: Partial<ScoreHistory>): ScoreHistory => ({
  ...latest({}), session_id: 's', segment: 0, window_start_offset_nanos: 0, window_end_offset_nanos: 1,
  scored_at: '2026-10-15T10:00:00Z', ...over,
})

describe('score view model', () => {
  it('groups the latest scores by game context, ordered by model', () => {
    const groups = groupByContext([
      latest({ game_context: 'sw', model_name: 'b' }),
      latest({ game_context: 'bw', model_name: 'z' }),
      latest({ game_context: 'bw', model_name: 'a' }),
    ])
    expect(groups.map(g => g.gameContext)).toEqual(['bw', 'sw'])
    expect(groups[0]!.rows.map(r => r.model_name)).toEqual(['a', 'z'])
  })

  it('names a series by model, version, context and domain', () => {
    expect(seriesKey(latest({}))).toBe('gbm@1 · bw · movement')
  })

  it('builds one series per model and turns the newest-first history around', () => {
    const series = buildSeries([
      hist({ scored_at: '2026-10-15T12:00:00Z', score: 0.9 }),
      hist({ scored_at: '2026-10-15T11:00:00Z', score: 0.1 }),
      hist({ model_name: 'other', scored_at: '2026-10-15T11:30:00Z' }),
    ])
    expect(series.map(s => s.key)).toEqual(['gbm@1 · bw · movement', 'other@1 · bw · movement'])
    expect(series[0]!.points.map(p => p.score)).toEqual([0.1, 0.9])
  })

  it('skips points with an unreadable timestamp', () => {
    expect(buildSeries([hist({ scored_at: 'not a date' })])).toEqual([])
  })

  it('maps scores to the fixed 0..1 axis and time to the width', () => {
    const g = DEFAULT_GEOMETRY
    const plot = plotSeries(buildSeries([
      hist({ scored_at: '2026-10-15T10:00:00Z', score: 0, smoothed: 0 }),
      hist({ scored_at: '2026-10-15T12:00:00Z', score: 1, smoothed: 1 }),
    ]))
    const top = g.margin.top
    const bottom = g.height - g.margin.bottom
    expect(plot.yTicks[0]).toMatchObject({ value: 0, y: bottom })
    expect(plot.yTicks.at(-1)).toMatchObject({ value: 1, y: top })
    expect(plot.series[0]!.smoothedPath).toBe(`M${g.margin.left.toFixed(1)} ${bottom.toFixed(1)} L${(g.width - g.margin.right).toFixed(1)} ${top.toFixed(1)}`)
    expect(plot.xTicks).toHaveLength(3)
  })

  it('clamps out-of-range scores and handles an empty history', () => {
    const plot = plotSeries(buildSeries([hist({ score: 7, smoothed: -3 })]))
    expect(plot.series[0]!.last!.y).toBe(DEFAULT_GEOMETRY.height - DEFAULT_GEOMETRY.margin.bottom)
    expect(plotSeries([])).toMatchObject({ series: [], xTicks: [] })
  })
})
