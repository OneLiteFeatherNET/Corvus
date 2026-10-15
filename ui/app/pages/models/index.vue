<template>
  <div>
    <PageHeader :title="$t('models.title')">
      <template #actions>
        <v-btn
          variant="tonal"
          prepend-icon="mdi-compare-horizontal"
          :disabled="selected.length !== 2"
          :to="compareLink"
        >
          {{ $t('models.compare') }}
        </v-btn>
      </template>
    </PageHeader>

    <v-row dense>
      <v-col cols="12" sm="6" md="2"><v-text-field v-model="name" :label="$t('models.name')" prepend-inner-icon="mdi-magnify" clearable hide-details /></v-col>
      <v-col cols="12" sm="6" md="2"><v-text-field v-model="version" :label="$t('models.version')" clearable hide-details /></v-col>
      <v-col cols="12" sm="6" md="3"><v-text-field v-model="gameContext" :label="$t('models.gameContext')" clearable hide-details /></v-col>
      <v-col cols="12" sm="6" md="3"><v-text-field v-model="domain" :label="$t('models.domain')" clearable hide-details /></v-col>
      <v-col cols="12" sm="6" md="2"><v-select v-model="stage" :items="stageItems" :label="$t('models.stageLabel')" hide-details /></v-col>
    </v-row>
    <p class="text-body-small text-on-surface-variant mt-1">{{ $t('models.filterHint') }}</p>

    <AppProblem :error="list.error.value" />
    <p class="text-body-medium text-on-surface-variant mt-3">{{ $t('models.compareHint') }}</p>
    <v-data-table
      v-model="selected"
      :headers="headers"
      :items="list.items.value"
      :loading="list.loading.value"
      :no-data-text="$t('common.empty')"
      item-value="id"
      show-select
      return-object
      hover
    >
      <template #[`item.name`]="{ item }">
        <NuxtLink :to="modelLocation(item)" class="text-primary font-weight-medium">{{ item.name }}</NuxtLink>
      </template>
      <template #[`item.stage`]="{ item }"><StatusChip kind="stage" :value="item.stage" /></template>
      <template #[`item.recall`]="{ item }">{{ formatMetric(metricValue(item.metrics, 'recall_at_fpr_0_1pct'), 'ratio') }}</template>
      <template #[`item.p99`]="{ item }">{{ formatMetric(metricValue(item.metrics, 'p99_latency_ms'), 'ms') }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </v-data-table>
    <v-btn v-if="list.hasMore()" class="mt-3" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'
import { formatMetric, metricValue } from '~/lib/metrics'
import { debounce } from '~/lib/debounce'
import { compareQuery } from '~/lib/model-list'
import type { Model, Stage } from '~/types'

const { t, locale } = useI18n()
const models = useModels()

const name = useQueryState('q')
const version = useQueryState('version')
const gameContext = useQueryState('game_context')
const domain = useQueryState('domain')
const stage = useQueryState('stage')
const selected = ref<Model[]>([])

// Every filter is applied by the API (exact match, combined with AND); typing is debounced.
const list = usePagedList(cursor => models.list({
  limit: 200,
  cursor,
  name: name.value?.trim(),
  version: version.value?.trim(),
  gameContext: gameContext.value?.trim(),
  domain: domain.value?.trim(),
  stage: (stage.value || undefined) as Stage | undefined,
}))
const reload = debounce(() => {
  selected.value = []
  void list.load()
}, 300)
onMounted(list.load)
onBeforeUnmount(reload.cancel)
watch([name, version, gameContext, domain], reload)
watch(stage, () => {
  reload()
  reload.flush()
})

const stageItems = computed(() => [
  { value: '', title: t('common.all') },
  ...(['registered', 'shadow', 'canary', 'enforce', 'archived'] as const).map(v => ({ value: v, title: t(`models.stage.${v}`) })),
])

const compareLink = computed(() => {
  const [a, b] = selected.value
  return a && b ? { path: '/models/compare', query: compareQuery(a, b) } : undefined
})
const headers = computed(() => [
  { title: t('models.name'), key: 'name' },
  { title: t('models.version'), key: 'version' },
  { title: t('models.gameContext'), key: 'game_context' },
  { title: t('models.domain'), key: 'domain' },
  { title: t('models.stageLabel'), key: 'stage' },
  { title: t('models.metrics.recallAtFpr'), key: 'recall', sortable: false },
  { title: t('models.metrics.p99'), key: 'p99', sortable: false },
  { title: t('common.created'), key: 'created_at' },
])

function modelLocation(m: Model) {
  return { name: 'models-name-version', params: { name: m.name, version: m.version } }
}
</script>
