<template>
  <div>
    <PageHeader :title="$t('models.compareTitle')" />
    <AppProblem :error="list.error.value" />

    <v-row>
      <v-col cols="12" md="6">
        <v-autocomplete v-model="refA" :items="options" item-title="title" item-value="value" :label="$t('models.compareA')" clearable />
      </v-col>
      <v-col cols="12" md="6">
        <v-autocomplete v-model="refB" :items="options" item-title="title" item-value="value" :label="$t('models.compareB')" clearable />
      </v-col>
    </v-row>

    <v-card v-if="modelA && modelB" variant="flat" color="surface-container-low">
      <v-table>
        <caption class="sr-only">{{ $t('models.compareTitle') }}</caption>
        <thead>
          <tr>
            <th scope="col">{{ $t('models.metricsTitle') }}</th>
            <th scope="col">{{ modelA.name }} {{ modelA.version }} <StatusChip kind="stage" :value="modelA.stage" /></th>
            <th scope="col">{{ modelB.name }} {{ modelB.version }} <StatusChip kind="stage" :value="modelB.stage" /></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.def.key">
            <th scope="row">{{ $t(`models.metrics.${row.def.label}`) }}</th>
            <td :class="{ 'font-weight-bold': row.winner === 'a' }">
              {{ formatMetric(row.a, row.def.format) }}
              <v-icon v-if="row.winner === 'a'" size="small" color="primary" icon="mdi-trophy-outline" :aria-label="$t('models.better')" />
            </td>
            <td :class="{ 'font-weight-bold': row.winner === 'b' }">
              {{ formatMetric(row.b, row.def.format) }}
              <v-icon v-if="row.winner === 'b'" size="small" color="primary" icon="mdi-trophy-outline" :aria-label="$t('models.better')" />
            </td>
          </tr>
          <tr>
            <th scope="row">{{ $t('models.gameContext') }}</th><td>{{ modelA.game_context }}</td><td>{{ modelB.game_context }}</td>
          </tr>
          <tr>
            <th scope="row">{{ $t('models.domain') }}</th><td>{{ modelA.domain }}</td><td>{{ modelB.domain }}</td>
          </tr>
          <tr>
            <th scope="row">{{ $t('jobs.snapshot') }}</th>
            <td class="mono">{{ shortId(modelA.snapshot_id) }}</td>
            <td class="mono">{{ shortId(modelB.snapshot_id) }}</td>
          </tr>
        </tbody>
      </v-table>
    </v-card>
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

const options = computed(() => list.items.value.map(m => ({ value: modelRef(m), title: `${m.name} ${m.version} (${m.stage})` })))
const modelA = computed(() => list.items.value.find(m => modelRef(m) === refA.value))
const modelB = computed(() => list.items.value.find(m => modelRef(m) === refB.value))
const rows = computed(() => (modelA.value && modelB.value ? compareModels(modelA.value.metrics, modelB.value.metrics) : []))
</script>
