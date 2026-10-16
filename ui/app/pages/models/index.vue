<template>
  <div>
    <PageHeader :title="$t('models.title')">
      <template #actions>
        <M3Button variant="tonal" icon="mdi-compare-horizontal" :disabled="selected.length !== 2" :to="compareLink">
          {{ $t('models.compare') }}
        </M3Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <M3TextField v-model="name" :label="$t('models.name')" leading-icon="mdi-magnify" clearable :clear-label="$t('common.delete')" />
      <M3TextField v-model="version" :label="$t('models.version')" clearable :clear-label="$t('common.delete')" />
      <M3TextField v-model="gameContext" :label="$t('models.gameContext')" clearable :clear-label="$t('common.delete')" />
      <M3TextField v-model="domain" :label="$t('models.domain')" clearable :clear-label="$t('common.delete')" />
      <M3Select v-model="stage" :items="stageItems" :label="$t('models.stageLabel')" />
    </div>
    <p class="mt-2 text-body-small text-on-surface-variant">{{ $t('models.filterHint') }}</p>

    <AppProblem :error="list.error.value" />
    <p class="my-3 text-body-medium text-on-surface-variant">{{ $t('models.compareHint') }}</p>
    <M3DataTable v-model:selected="selected" :columns="headers" :items="list.items.value" :loading="list.loading.value" selectable>
      <template #[`item.name`]="{ item }">
        <NuxtLink :to="modelLocation(item)" class="font-medium">{{ item.name }}</NuxtLink>
      </template>
      <template #[`item.stage`]="{ item }"><StatusChip kind="stage" :value="item.stage" /></template>
      <template #[`item.recall`]="{ item }">{{ formatMetric(metricValue(item.metrics, 'recall_at_fpr_0_1pct'), 'ratio') }}</template>
      <template #[`item.p99`]="{ item }">{{ formatMetric(metricValue(item.metrics, 'p99_latency_ms'), 'ms') }}</template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />
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
