// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ScoreChart from './ScoreChart.vue'
import type { ChartSeries } from '~/lib/score-view'

const labels = {
  title: 'Score history', desc: 'Line chart of smoothed scores.', summary: '2 models, 4 data points', time: 'Time',
  model: 'Model', smoothed: 'Smoothed', raw: 'Raw value', dataTable: 'Values as table',
}

const series: ChartSeries[] = [
  { key: 'gbm@1 · bw · movement', stage: 'enforce', points: [
    { t: Date.parse('2026-10-15T10:00:00Z'), score: 0.2, smoothed: 0.25 },
    { t: Date.parse('2026-10-15T11:00:00Z'), score: 0.8, smoothed: 0.6 },
  ] },
  { key: 'cnn@2 · bw · combat', stage: 'shadow', points: [
    { t: Date.parse('2026-10-15T10:30:00Z'), score: 0.4, smoothed: 0.4 },
    { t: Date.parse('2026-10-15T11:30:00Z'), score: 0.5, smoothed: 0.45 },
  ] },
]

describe('ScoreChart', () => {
  const wrapper = () => mount(ScoreChart, { props: { series, labels, locale: 'en' } })

  it('exposes the chart to assistive technology with a title and a description', () => {
    const svg = wrapper().get('svg[role="img"]')
    const ids = svg.attributes('aria-labelledby')!.split(' ')
    expect(ids).toHaveLength(2)
    const html = svg.html()
    expect(html).toContain('Score history')
    expect(html).toContain('Line chart of smoothed scores. 2 models, 4 data points')
    for (const id of ids) expect(svg.find(`[id="${id}"]`).exists()).toBe(true)
  })

  it('draws a smoothed and a raw line for every model', () => {
    const w = wrapper()
    expect(w.findAll('path.smoothed')).toHaveLength(2)
    expect(w.findAll('path.raw')).toHaveLength(2)
    expect(w.findAll('path.smoothed')[0]!.attributes('d')).toMatch(/^M[\d.]+ [\d.]+ L[\d.]+ [\d.]+$/)
  })

  it('tells the series apart by more than color: legend names and distinct dash classes', () => {
    const w = wrapper()
    expect(w.findAll('.legend li').map(li => li.text())).toEqual(['gbm@1 · bw · movement', 'cnn@2 · bw · combat'])
    const classes = w.findAll('g.series').map(g => g.classes().filter(c => /^d\d$/.test(c)))
    expect(classes[0]).not.toEqual(classes[1])
  })

  it('offers all values as a table', () => {
    const rows = wrapper().findAll('table.data tbody tr')
    expect(rows).toHaveLength(4)
    expect(rows[0]!.text()).toContain('0.200')
    expect(rows[0]!.text()).toContain('0.250')
  })
})
