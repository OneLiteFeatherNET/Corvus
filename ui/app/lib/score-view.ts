import type { ScoreHistory, ScoreLatest } from '~/types'

/** Stable key of one model series: a score belongs to model, version, game context and domain. */
export function seriesKey(s: Pick<ScoreLatest, 'model_name' | 'model_version' | 'game_context' | 'domain'>): string {
  return `${s.model_name}@${s.model_version} · ${s.game_context} · ${s.domain}`
}

/** Latest scores grouped by game context, rows ordered by model. */
export function groupByContext(items: ScoreLatest[]): Array<{ gameContext: string, rows: ScoreLatest[] }> {
  const groups = new Map<string, ScoreLatest[]>()
  for (const i of items) groups.set(i.game_context, [...(groups.get(i.game_context) ?? []), i])
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([gameContext, rows]) => ({ gameContext, rows: [...rows].sort((a, b) => seriesKey(a).localeCompare(seriesKey(b))) }))
}

export interface ChartPoint {
  t: number
  score: number
  smoothed: number
}

export interface ChartSeries {
  key: string
  stage: string
  points: ChartPoint[]
}

/** The history is newest first; the chart wants each series oldest first. */
export function buildSeries(history: ScoreHistory[]): ChartSeries[] {
  const map = new Map<string, ChartSeries>()
  for (const h of history) {
    const t = Date.parse(h.scored_at)
    if (Number.isNaN(t)) continue
    const key = seriesKey(h)
    const series = map.get(key) ?? { key, stage: h.stage, points: [] }
    series.points.push({ t, score: h.score, smoothed: h.smoothed })
    map.set(key, series)
  }
  for (const s of map.values()) s.points.sort((a, b) => a.t - b.t)
  return [...map.values()].sort((a, b) => a.key.localeCompare(b.key))
}

export interface ChartGeometry {
  width: number
  height: number
  margin: { top: number, right: number, bottom: number, left: number }
}

export const DEFAULT_GEOMETRY: ChartGeometry = { width: 640, height: 260, margin: { top: 12, right: 16, bottom: 28, left: 40 } }

export interface PlottedSeries {
  key: string
  stage: string
  smoothedPath: string
  scorePath: string
  last: { x: number, y: number } | null
}

export interface Plot {
  series: PlottedSeries[]
  yTicks: Array<{ value: number, y: number }>
  xTicks: Array<{ t: number, x: number }>
  tMin: number
  tMax: number
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** Maps series to SVG coordinates. The score axis is fixed to 0..1; time spans the data. */
export function plotSeries(series: ChartSeries[], g: ChartGeometry = DEFAULT_GEOMETRY): Plot {
  const times = series.flatMap(s => s.points.map(p => p.t))
  const tMin = times.length ? Math.min(...times) : 0
  const tMax = times.length ? Math.max(...times) : 0
  const innerW = g.width - g.margin.left - g.margin.right
  const innerH = g.height - g.margin.top - g.margin.bottom
  const x = (t: number) => g.margin.left + (tMax === tMin ? innerW / 2 : ((t - tMin) / (tMax - tMin)) * innerW)
  const y = (v: number) => g.margin.top + (1 - clamp01(v)) * innerH
  const path = (pts: Array<[number, number]>) =>
    pts.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px.toFixed(1)} ${py.toFixed(1)}`).join(' ')

  const plotted = series.map((s) => {
    const smoothed = s.points.map((p): [number, number] => [x(p.t), y(p.smoothed)])
    const last = smoothed.at(-1)
    return {
      key: s.key,
      stage: s.stage,
      smoothedPath: path(smoothed),
      scorePath: path(s.points.map((p): [number, number] => [x(p.t), y(p.score)])),
      last: last ? { x: last[0], y: last[1] } : null,
    }
  })
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(value => ({ value, y: y(value) }))
  const xTicks = times.length
    ? [tMin, ...(tMax === tMin ? [] : [tMin + (tMax - tMin) / 2, tMax])].map(t => ({ t, x: x(t) }))
    : []
  return { series: plotted, yTicks, xTicks, tMin, tMax }
}
