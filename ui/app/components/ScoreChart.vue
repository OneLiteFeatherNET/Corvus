<template>
  <figure ref="root" class="score-chart">
    <svg
      :viewBox="`0 0 ${geo.width} ${geo.height}`"
      role="img"
      :aria-labelledby="`${uid}-title ${uid}-desc`"
      preserveAspectRatio="xMidYMid meet"
    >
      <title :id="`${uid}-title`">{{ labels.title }}</title>
      <desc :id="`${uid}-desc`">{{ labels.desc }} {{ summary }}</desc>
      <g class="grid" aria-hidden="true">
        <line
          v-for="tick in plot.yTicks"
          :key="tick.value"
          :x1="geo.margin.left"
          :x2="geo.width - geo.margin.right"
          :y1="tick.y"
          :y2="tick.y"
        />
        <text
          v-for="tick in plot.yTicks"
          :key="`l${tick.value}`"
          class="axis"
          :x="geo.margin.left - 6"
          :y="tick.y + 4"
          text-anchor="end"
        >{{ tick.value }}</text>
        <text
          v-for="(tick, i) in plot.xTicks"
          :key="tick.t"
          class="axis"
          :x="tick.x"
          :y="geo.height - 8"
          :text-anchor="anchorFor(i)"
        >{{ formatTick(tick.t) }}</text>
      </g>
      <g v-for="(s, i) in plot.series" :key="s.key" :class="['series', `c${i % COLORS}`, `d${i % DASHES}`]" aria-hidden="true">
        <path class="raw" :d="s.scorePath" />
        <path class="smoothed" :d="s.smoothedPath" />
        <circle v-if="s.last" :cx="s.last.x" :cy="s.last.y" r="3.5" />
      </g>
    </svg>

    <ul class="legend" :aria-label="labels.title">
      <li v-for="(s, i) in plot.series" :key="s.key">
        <svg width="28" height="10" aria-hidden="true" :class="['series', `c${i % COLORS}`, `d${i % DASHES}`]">
          <line class="smoothed" x1="0" x2="28" y1="5" y2="5" />
        </svg>
        <span>{{ s.key }}</span>
      </li>
    </ul>
    <p class="text-body-small text-on-surface-variant">
      <span class="legend-raw" aria-hidden="true" /> {{ labels.raw }}, <strong>{{ labels.smoothed }}</strong>
    </p>

    <details class="mt-2">
      <summary>{{ labels.dataTable }}</summary>
      <table class="data">
        <thead>
          <tr><th scope="col">{{ labels.model }}</th><th scope="col">{{ labels.time }}</th><th scope="col">{{ labels.raw }}</th><th scope="col">{{ labels.smoothed }}</th></tr>
        </thead>
        <tbody>
          <template v-for="s in series" :key="s.key">
            <tr v-for="p in s.points" :key="`${s.key}-${p.t}`">
              <th scope="row">{{ s.key }}</th>
              <td>{{ formatTick(p.t) }}</td>
              <td>{{ p.score.toFixed(3) }}</td>
              <td>{{ p.smoothed.toFixed(3) }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </details>
  </figure>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { DEFAULT_GEOMETRY, plotSeries } from '~/lib/score-view'
import type { ChartGeometry, ChartSeries } from '~/lib/score-view'

export interface ScoreChartLabels {
  title: string
  desc: string
  summary: string
  time: string
  model: string
  smoothed: string
  raw: string
  dataTable: string
}

const props = defineProps<{ series: ChartSeries[], labels: ScoreChartLabels, locale?: string }>()

const COLORS = 5
const DASHES = 3
const uid = useId()

// The chart is drawn at the width of its container (1 SVG unit = 1 CSS pixel), so text keeps its size.
const root = ref<HTMLElement>()
const width = ref(DEFAULT_GEOMETRY.width)
const geo = computed<ChartGeometry>(() => {
  const w = Math.max(280, width.value)
  return { ...DEFAULT_GEOMETRY, width: w, height: Math.round(Math.min(340, Math.max(200, w * 0.36))), margin: { ...DEFAULT_GEOMETRY.margin, right: 24 } }
})
let observer: ResizeObserver | undefined
onMounted(() => {
  if (typeof ResizeObserver === 'undefined' || !root.value) return
  observer = new ResizeObserver(([entry]) => {
    if (entry && entry.contentRect.width > 0) width.value = Math.floor(entry.contentRect.width)
  })
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())

const plot = computed(() => plotSeries(props.series, geo.value))
// The first and the last tick label stay inside the plot.
const anchorFor = (i: number) => (i === 0 ? 'start' : i === plot.value.xTicks.length - 1 ? 'end' : 'middle')
const summary = computed(() => props.labels.summary)
const fmt = computed(() => new Intl.DateTimeFormat(props.locale ?? 'de', { dateStyle: 'short', timeStyle: 'short' }))
const formatTick = (t: number) => fmt.value.format(new Date(t))
</script>

<style scoped>
.score-chart {
  margin: 0;
}
figure > svg {
  width: 100%;
  height: auto;
  display: block;
}
.grid line {
  stroke: var(--md-sys-color-outline-variant);
}
.axis {
  fill: var(--md-sys-color-on-surface-variant);
  font-size: 11px;
}
.series {
  fill: none;
  --c: var(--md-sys-color-primary);
  stroke: var(--c);
}
.c0 { --c: var(--md-sys-color-primary); }
.c1 { --c: var(--md-sys-color-tertiary); }
.c2 { --c: var(--md-sys-color-success); }
.c3 { --c: var(--md-sys-color-error); }
.c4 { --c: var(--md-sys-color-warning); }
.series .smoothed {
  fill: none;
  stroke: var(--c);
  stroke-width: 2.5;
}
.d1 .smoothed { stroke-dasharray: 8 4; }
.d2 .smoothed { stroke-dasharray: 2 4; stroke-linecap: round; }
.series .raw {
  fill: none;
  stroke: var(--c);
  stroke-width: 1;
  opacity: 0.4;
}
.series circle {
  fill: var(--c);
  stroke: var(--md-sys-color-surface);
}
.legend {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  padding: 0;
  margin: 8px 0 4px;
}
.legend li {
  display: flex;
  align-items: center;
  gap: 8px;
}
.legend-raw {
  display: inline-block;
  width: 20px;
  border-top: 1px solid currentColor;
  opacity: 0.5;
  vertical-align: middle;
}
summary {
  cursor: pointer;
}
.data {
  border-collapse: collapse;
  margin-top: 8px;
  font-size: 0.875rem;
}
.data th,
.data td {
  text-align: left;
  padding: 2px 12px 2px 0;
}
</style>
