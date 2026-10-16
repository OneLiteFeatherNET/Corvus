<template>
  <div>
    <PageHeader :title="$t('scores.title')" :subtitle="$t('scores.subtitle')" />

    <M3Alert tone="info" class="mb-6">{{ $t('scores.disclaimer') }}</M3Alert>

    <M3Card class="mb-6">
      <form class="flex flex-wrap items-start gap-3" @submit.prevent="submit">
        <M3TextField
          v-model="input"
          :label="$t('scores.pseudonym')"
          :error="input && !isPseudonym(input) ? $t('scores.invalidPseudonym') : ''"
          autocomplete="off"
          spellcheck="false"
          input-class="mono"
          class="min-w-64 flex-1"
        />
        <M3Button type="submit" class="mt-2" :disabled="!isPseudonym(input)" :loading="loading" :loading-label="$t('common.loading')" icon="mdi-chart-line">
          {{ $t('scores.submit') }}
        </M3Button>
      </form>
    </M3Card>

    <AppProblem :error="error" />

    <div aria-live="polite">
      <template v-if="loaded">
        <h2 class="mb-1 text-headline-small">{{ $t('scores.latest') }}</h2>
        <p class="mb-3 text-body-medium text-on-surface-variant">{{ $t('scores.stageHint') }}</p>
        <p v-if="!latest.length" class="text-on-surface-variant">{{ $t('scores.empty') }}</p>
        <template v-else>
          <div v-for="g in groups" :key="g.gameContext" class="mb-4 overflow-x-auto rounded-md border border-outline-variant">
            <table class="w-full border-collapse text-body-medium">
              <caption class="px-4 py-3 text-title-medium">{{ g.gameContext }}</caption>
              <thead>
                <tr class="bg-surface-container text-title-small text-on-surface-variant">
                  <th scope="col" class="h-12 px-4 text-start">{{ $t('scores.model') }}</th>
                  <th scope="col" class="px-4 text-start">{{ $t('scores.domain') }}</th>
                  <th scope="col" class="px-4 text-start">{{ $t('scores.stage') }}</th>
                  <th scope="col" class="px-4 text-end">{{ $t('scores.score') }}</th>
                  <th scope="col" class="px-4 text-end">{{ $t('scores.smoothed') }}</th>
                  <th scope="col" class="px-4 text-end">{{ $t('scores.windows') }}</th>
                  <th scope="col" class="px-4 text-start">{{ $t('scores.updatedAt') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in g.rows" :key="seriesKey(s)" class="border-t border-outline-variant">
                  <th scope="row" class="px-4 py-3 text-start font-medium">{{ s.model_name }} {{ s.model_version }}</th>
                  <td class="px-4">{{ s.domain }}</td>
                  <td class="px-4"><StatusChip kind="stage" :value="s.stage" /></td>
                  <td class="px-4 text-end">{{ s.score.toFixed(3) }}</td>
                  <td class="px-4 text-end">{{ s.smoothed.toFixed(3) }}</td>
                  <td class="px-4 text-end">{{ formatNumber(s.windows, locale) }}</td>
                  <td class="px-4 whitespace-nowrap">{{ formatDateTime(s.updated_at, locale) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <h2 class="mt-8 mb-1 text-headline-small">{{ $t('scores.history') }}</h2>
        <p class="mb-3 text-body-medium text-on-surface-variant">{{ $t('scores.retention') }}</p>
        <div class="mb-4 flex flex-wrap gap-3">
          <M3Select v-model="context" :items="contextItems" :label="$t('scores.historyContext')" class="w-full sm:w-72" />
          <M3Select v-model="limit" :items="LIMITS" :label="$t('scores.historyLimit')" class="w-full sm:w-40" />
        </div>
        <M3Progress v-if="historyLoading" :label="$t('common.loading')" />
        <p v-if="!series.length && !historyLoading" class="text-on-surface-variant">{{ $t('scores.historyEmpty') }}</p>
        <ScoreChart v-else-if="series.length" :series="series" :labels="chartLabels" :locale="locale" />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { isPseudonym } from '~/lib/erasure'
import { formatDateTime, formatNumber } from '~/lib/format'
import { buildSeries, groupByContext, seriesKey } from '~/lib/score-view'
import type { ScoreHistory, ScoreLatest } from '~/types'

const LIMITS = [50, 100, 250, 500, 1000]

const { t, locale } = useI18n()
const scores = useScores()

const pseudonym = useQueryState('pseudonym')
const input = ref(pseudonym.value)
const context = ref('')
const limit = ref(100)

const latest = ref<ScoreLatest[]>([])
const history = ref<ScoreHistory[]>([])
const loaded = ref(false)
const loading = ref(false)
const historyLoading = ref(false)
const error = ref<Error | null>(null)

// A newer request aborts the previous one, so a slow answer for a former pseudonym is never shown.
let latestCall: AbortController | undefined
let historyCall: AbortController | undefined

const groups = computed(() => groupByContext(latest.value))
const series = computed(() => buildSeries(history.value))
const contextItems = computed(() => [
  { value: '', title: t('common.all') },
  ...groups.value.map(g => ({ value: g.gameContext, title: g.gameContext })),
])
const chartLabels = computed(() => ({
  title: t('scores.chart.title'),
  desc: t('scores.chart.desc'),
  summary: t('scores.chart.summary', { series: series.value.length, points: history.value.length }),
  time: t('scores.chart.time'),
  model: t('scores.model'),
  smoothed: t('scores.chart.smoothed'),
  raw: t('scores.chart.raw'),
  dataTable: t('scores.chart.dataTable'),
}))

function asError(e: unknown): Error | null {
  if (e instanceof DOMException && e.name === 'AbortError') return null
  return e instanceof Error ? e : new Error(String(e))
}

async function loadHistory() {
  const id = pseudonym.value
  if (!isPseudonym(id)) return
  historyCall?.abort()
  const call = (historyCall = new AbortController())
  historyLoading.value = true
  try {
    const res = await scores.history(id, { gameContext: context.value || undefined, limit: limit.value }, call.signal)
    if (!call.signal.aborted) history.value = res.items
  }
  catch (e) {
    const err = asError(e)
    if (err && !call.signal.aborted) error.value = err
  }
  finally {
    if (historyCall === call) historyLoading.value = false
  }
}

async function loadLatest() {
  const id = pseudonym.value
  if (!isPseudonym(id)) return
  latestCall?.abort()
  const call = (latestCall = new AbortController())
  loading.value = true
  error.value = null
  try {
    const [res] = await Promise.all([scores.latest(id, call.signal), loadHistory()])
    if (!call.signal.aborted) {
      latest.value = res.items
      loaded.value = true
    }
  }
  catch (e) {
    const err = asError(e)
    if (err && !call.signal.aborted) error.value = err
  }
  finally {
    if (latestCall === call) loading.value = false
  }
}

function submit() {
  if (!isPseudonym(input.value)) return
  pseudonym.value = input.value.trim().toLowerCase()
  context.value = ''
  void loadLatest()
}

watch([context, limit], () => {
  if (loaded.value) void loadHistory()
})
onMounted(() => {
  if (isPseudonym(pseudonym.value)) void loadLatest()
})
onBeforeUnmount(() => {
  latestCall?.abort()
  historyCall?.abort()
})
</script>
