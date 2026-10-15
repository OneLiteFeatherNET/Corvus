<template>
  <div>
    <AppProblem :error="loadError" />
    <v-progress-linear v-if="!model && !loadError" indeterminate :aria-label="$t('common.loading')" />
    <template v-if="model">
      <PageHeader :title="`${model.name} ${model.version}`" :subtitle="`${model.game_context} · ${model.domain} · ${model.candidate_type}`">
        <template #actions>
          <StatusChip kind="stage" :value="model.stage" />
          <v-btn v-if="can('models.transition')" prepend-icon="mdi-swap-horizontal" @click="stageDialog = true">{{ $t('models.transition.open') }}</v-btn>
          <v-btn variant="tonal" prepend-icon="mdi-compare-horizontal" :to="{ path: '/models/compare', query: { a: modelRef(model) } }">{{ $t('models.compare') }}</v-btn>
        </template>
      </PageHeader>

      <v-row>
        <v-col cols="12" md="6">
          <v-card variant="flat" color="surface-container-low" class="h-100">
            <v-card-title class="text-title-large">{{ $t('models.metricsTitle') }}</v-card-title>
            <v-card-text>
              <dl class="kv">
                <template v-for="m in METRICS" :key="m.key">
                  <dt>{{ $t(`models.metrics.${m.label}`) }}</dt>
                  <dd>{{ formatMetric(metricValue(model.metrics, m.key), m.format) }}</dd>
                </template>
              </dl>
            </v-card-text>
          </v-card>
        </v-col>
        <v-col cols="12" md="6">
          <v-card variant="flat" color="surface-container-low" class="h-100">
            <v-card-title class="text-title-large">{{ $t('models.details') }}</v-card-title>
            <v-card-text>
              <dl class="kv">
                <dt>ID</dt><dd class="mono">{{ model.id }}</dd>
                <dt>{{ $t('jobs.snapshot') }}</dt><dd><NuxtLink :to="`/snapshots/${model.snapshot_id}`" class="mono">{{ model.snapshot_id }}</NuxtLink></dd>
                <dt>ONNX</dt><dd class="mono">{{ model.onnx_key }}</dd>
                <dt>{{ $t('common.created') }}</dt><dd>{{ formatDateTime(model.created_at, locale) }} · {{ model.created_by }}</dd>
              </dl>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <h2 class="text-headline-small mt-8 mb-3">{{ $t('models.history') }}</h2>
      <v-data-table :headers="historyHeaders" :items="transitions" :no-data-text="$t('common.empty')" item-value="id">
        <template #[`item.from_stage`]="{ item }"><StatusChip kind="stage" :value="item.from_stage" /></template>
        <template #[`item.to_stage`]="{ item }"><StatusChip kind="stage" :value="item.to_stage" /></template>
        <template #[`item.at`]="{ item }">{{ formatDateTime(item.at, locale) }}</template>
      </v-data-table>

      <StageDialog v-if="can('models.transition')" v-model="stageDialog" :model="model" @changed="onChanged" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'
import { ApiError } from '~/lib/problem'
import { formatMetric, METRICS, metricValue } from '~/lib/metrics'
import { modelRef } from '~/lib/model-list'
import type { Model, ModelTransition } from '~/types'

const route = useRoute()
const { can } = useAuth()
const { t, locale } = useI18n()
const models = useModels()

const model = ref<Model>()
const transitions = ref<ModelTransition[]>([])
const loadError = ref<Error | null>(null)
const stageDialog = ref(false)

const historyHeaders = computed(() => [
  { title: t('models.from'), key: 'from_stage' },
  { title: t('models.to'), key: 'to_stage' },
  { title: t('models.actor'), key: 'actor' },
  { title: t('models.reason'), key: 'reason' },
  { title: t('common.created'), key: 'at' },
])

async function loadTransitions() {
  if (model.value) transitions.value = await models.transitions(model.value.id)
}

async function onChanged(updated: Model) {
  model.value = updated
  await loadTransitions()
}

onMounted(async () => {
  try {
    model.value = await models.byRef(String(route.params.name), String(route.params.version))
    await loadTransitions()
  }
  catch (e) {
    loadError.value = e instanceof ApiError && e.status === 404 ? new Error(t('models.notFound')) : e instanceof Error ? e : new Error(String(e))
  }
})
</script>

<style scoped>
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 8px 24px;
}
.kv dt {
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
