<template>
  <div>
    <PageHeader :title="$t('models.compareTitle')" />
    <AppProblem :error="list.error.value" />

    <div class="mb-6 grid grid-cols-1 gap-3 md:grid-cols-2">
      <M3Select v-model="refA" :items="options" :label="$t('models.compareA')" />
      <M3Select v-model="refB" :items="options" :label="$t('models.compareB')" />
    </div>

    <div v-if="modelA && modelB" class="overflow-x-auto rounded-md border border-outline-variant">
      <table class="w-full border-collapse text-body-medium">
        <caption class="sr-only">{{ $t('models.compareTitle') }}</caption>
        <thead>
          <tr class="bg-surface-container text-title-small text-on-surface-variant">
            <th scope="col" class="h-12 px-4 text-start">{{ $t('models.metricsTitle') }}</th>
            <th scope="col" class="px-4 text-start">{{ modelA.name }} {{ modelA.version }} <StatusChip kind="stage" :value="modelA.stage" /></th>
            <th scope="col" class="px-4 text-start">{{ modelB.name }} {{ modelB.version }} <StatusChip kind="stage" :value="modelB.stage" /></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.def.key" class="border-t border-outline-variant">
            <th scope="row" class="px-4 py-3 text-start font-normal">{{ $t(`models.metrics.${row.def.label}`) }}</th>
            <td :class="['px-4', row.winner === 'a' ? 'font-bold' : '']">
              {{ formatMetric(row.a, row.def.format) }}
              <M3Icon v-if="row.winner === 'a'" name="mdi-trophy-outline" :size="18" :label="$t('models.better')" class="text-primary" />
            </td>
            <td :class="['px-4', row.winner === 'b' ? 'font-bold' : '']">
              {{ formatMetric(row.b, row.def.format) }}
              <M3Icon v-if="row.winner === 'b'" name="mdi-trophy-outline" :size="18" :label="$t('models.better')" class="text-primary" />
            </td>
          </tr>
          <tr class="border-t border-outline-variant">
            <th scope="row" class="px-4 py-3 text-start font-normal">{{ $t('models.gameContext') }}</th><td class="px-4">{{ modelA.game_context }}</td><td class="px-4">{{ modelB.game_context }}</td>
          </tr>
          <tr class="border-t border-outline-variant">
            <th scope="row" class="px-4 py-3 text-start font-normal">{{ $t('models.domain') }}</th><td class="px-4">{{ modelA.domain }}</td><td class="px-4">{{ modelB.domain }}</td>
          </tr>
          <tr class="border-t border-outline-variant">
            <th scope="row" class="px-4 py-3 text-start font-normal">{{ $t('jobs.snapshot') }}</th>
            <td class="mono px-4">{{ shortId(modelA.snapshot_id) }}</td>
            <td class="mono px-4">{{ shortId(modelB.snapshot_id) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="text-on-surface-variant">{{ $t('models.comparePick') }}</p>
  </div>
</template>

<script setup lang="ts">
import { shortId } from '~/lib/format'
import { compareModels, formatMetric } from '~/lib/metrics'
import { modelRef } from '~/lib/model-list'

const models = useModels()
const refA = useQueryState('a')
const refB = useQueryState('b')

const list = usePagedList(cursor => models.list({ limit: 200, cursor }))
onMounted(async () => {
  await list.load()
  // The compare view needs the whole registry to resolve name@version; fetch the remaining pages.
  while (list.hasMore() && !list.error.value) await list.loadMore()
})

const options = computed(() => [{ value: '', title: '–' }, ...list.items.value.map(m => ({ value: modelRef(m), title: `${m.name} ${m.version} (${m.stage})` }))])
const modelA = computed(() => list.items.value.find(m => modelRef(m) === refA.value))
const modelB = computed(() => list.items.value.find(m => modelRef(m) === refB.value))
const rows = computed(() => (modelA.value && modelB.value ? compareModels(modelA.value.metrics, modelB.value.metrics) : []))
</script>
