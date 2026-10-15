<template>
  <div>
    <PageHeader :title="$t('scores.title')" :subtitle="$t('scores.subtitle')" />

    <v-alert type="info" variant="tonal" density="comfortable" class="mb-6" icon="mdi-information-outline">
      {{ $t('scores.disclaimer') }}
    </v-alert>

    <v-card variant="flat" color="surface-container-low" class="mb-6">
      <v-card-text>
        <v-form class="d-flex flex-wrap ga-3 align-start" @submit.prevent="submit">
          <v-text-field
            v-model="input"
            :label="$t('scores.pseudonym')"
            :error-messages="input && !isPseudonym(input) ? $t('scores.invalidPseudonym') : ''"
            autocomplete="off"
            spellcheck="false"
            class="mono"
            style="min-width: 320px; flex: 1"
          />
          <v-btn type="submit" :disabled="!isPseudonym(input)" :loading="loading" prepend-icon="mdi-chart-line" class="mt-1">
            {{ $t('scores.submit') }}
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>

    <AppProblem :error="error" />

    <div aria-live="polite">
      <template v-if="loaded">
        <h2 class="text-headline-small mb-1">{{ $t('scores.latest') }}</h2>
        <p class="text-body-medium text-on-surface-variant mb-3">{{ $t('scores.stageHint') }}</p>
        <p v-if="!latest.length" class="text-on-surface-variant">{{ $t('scores.empty') }}</p>
        <template v-else>
          <v-table v-for="g in groups" :key="g.gameContext" class="mb-4" density="comfortable">
            <caption class="text-title-medium text-start pa-2">{{ g.gameContext }}</caption>
            <thead>
              <tr>
                <th scope="col">{{ $t('scores.model') }}</th>
                <th scope="col">{{ $t('scores.domain') }}</th>
                <th scope="col">{{ $t('scores.stage') }}</th>
                <th scope="col" class="text-end">{{ $t('scores.score') }}</th>
                <th scope="col" class="text-end">{{ $t('scores.smoothed') }}</th>
                <th scope="col" class="text-end">{{ $t('scores.windows') }}</th>
                <th scope="col">{{ $t('scores.updatedAt') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in g.rows" :key="seriesKey(s)">
                <th scope="row">{{ s.model_name }} {{ s.model_version }}</th>
                <td>{{ s.domain }}</td>
                <td><StatusChip kind="stage" :value="s.stage" /></td>
                <td class="text-end">{{ s.score.toFixed(3) }}</td>
                <td class="text-end">{{ s.smoothed.toFixed(3) }}</td>
                <td class="text-end">{{ formatNumber(s.windows, locale) }}</td>
                <td>{{ formatDateTime(s.updated_at, locale) }}</td>
              </tr>
            </tbody>
          </v-table>
        </template>

        <h2 class="text-headline-small mt-8 mb-1">{{ $t('scores.history') }}</h2>
        <p class="text-body-medium text-on-surface-variant mb-3">{{ $t('scores.retention') }}</p>
        <v-row dense class="mb-2">
          <v-col cols="12" sm="6" md="4">
            <v-select v-model="context" :items="contextItems" :label="$t('scores.historyContext')" hide-details />
          </v-col>
          <v-col cols="12" sm="6" md="3">
            <v-select v-model="limit" :items="LIMITS" :label="$t('scores.historyLimit')" hide-details />
          </v-col>
        </v-row>
        <v-progress-linear v-if="historyLoading" indeterminate :aria-label="$t('common.loading')" />
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
