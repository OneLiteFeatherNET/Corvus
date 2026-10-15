<template>
  <div>
    <PageHeader :title="$t('dashboard.title')" :subtitle="$t('dashboard.subtitle')">
      <template #actions>
        <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="load">{{ $t('common.refresh') }}</v-btn>
      </template>
    </PageHeader>

    <AppProblem :error="error" />

    <v-row>
      <v-col v-for="tile in tiles" :key="tile.key" cols="12" sm="6" md="3">
        <v-card :to="tile.to" variant="flat" color="surface-container-low" class="h-100">
          <v-card-text>
            <div class="text-label-large text-on-surface-variant">{{ $t(`dashboard.${tile.key}`) }}</div>
            <div class="text-headline-large">{{ tile.value }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row v-if="can('models.read')" class="mt-2">
      <v-col cols="12">
        <v-card variant="flat" color="surface-container-low">
          <v-card-title class="text-title-large">{{ $t('dashboard.modelsPerStage') }}</v-card-title>
          <v-card-text class="d-flex flex-wrap ga-3">
            <v-chip
              v-for="s in STAGES"
              :key="s"
              :to="{ path: '/models', query: { stage: s } }"
              :color="stageColor(s)"
              :prepend-icon="stageIcon(s)"
              variant="tonal"
              label
            >
              {{ $t(`models.stage.${s}`) }}: {{ stageCounts[s] ?? 0 }}
            </v-chip>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row class="mt-2">
      <v-col v-if="can('jobs.read')" cols="12" md="6">
        <v-card variant="flat" color="surface-container-low">
          <v-card-title class="text-title-large">{{ $t('dashboard.recentJobs') }}</v-card-title>
          <v-list bg-color="transparent">
            <v-list-item v-for="j in recentJobs" :key="j.id" :to="`/jobs/${j.id}`" :title="`${$t(`jobs.type.${j.type}`)} · ${shortId(j.id)}`" :subtitle="formatDateTime(j.created_at, locale)">
              <template #append><StatusChip kind="job" :value="j.status" /></template>
            </v-list-item>
            <v-list-item v-if="!recentJobs.length" :title="$t('common.empty')" />
          </v-list>
        </v-card>
      </v-col>
      <v-col v-if="can('players.manage')" cols="12" md="6">
        <v-card variant="flat" color="surface-container-low">
          <v-card-title class="text-title-large">{{ $t('dashboard.recentErasures') }}</v-card-title>
          <v-list bg-color="transparent">
            <v-list-item v-for="e in erasures" :key="e.id" :title="`${e.pseudonym_prefix}…`" :subtitle="formatDateTime(e.requested_at, locale)">
              <template #append>
                <v-chip size="small" label variant="tonal" :color="e.compaction_pending ? 'warning' : 'default'">
                  {{ e.compaction_pending ? $t('dashboard.compactionPending') : $t('dashboard.compacted') }}
                </v-chip>
              </template>
            </v-list-item>
            <v-list-item v-if="!erasures.length" :title="$t('common.empty')" />
          </v-list>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { modelTotal, runningJobs, STAGES, stageCounts as countStages } from '~/lib/dashboard'
import { formatDateTime, formatNumber, shortId, stageColor, stageIcon } from '~/lib/format'
import type { ErasureBrief, JobBrief } from '~/types'

const { can } = useAuth()
const { locale } = useI18n()
const summaryApi = useSummary()

const loading = ref(false)
const error = ref<Error | null>(null)
const counts = reactive<Record<string, string>>({ pools: '–', snapshots: '–', running: '–', models: '–', pending: '–' })
const stageCounts = ref<Record<string, number>>({})
const recentJobs = ref<JobBrief[]>([])
const erasures = ref<ErasureBrief[]>([])

interface Tile { key: string, to: string | { path: string, query: Record<string, string> }, value: string }
const tiles = computed(() => {
  const out: Tile[] = []
  if (can('pools.read')) out.push({ key: 'pools', to: '/pools', value: counts.pools ?? '–' })
  if (can('snapshots.read')) out.push({ key: 'snapshots', to: '/snapshots', value: counts.snapshots ?? '–' })
  if (can('jobs.read')) out.push({ key: 'runningJobs', to: { path: '/jobs', query: { status: 'running' } }, value: counts.running ?? '–' })
  if (can('models.read')) out.push({ key: 'models', to: '/models', value: counts.models ?? '–' })
  if (can('players.manage')) out.push({ key: 'pendingErasures', to: '/players', value: counts.pending ?? '–' })
  return out
})

// One request replaces the former fan-out over every list endpoint.
async function load() {
  loading.value = true
  error.value = null
  try {
    const s = await summaryApi.get()
    const n = (v: number) => formatNumber(v, locale.value)
    counts.pools = n(s.pools)
    counts.snapshots = n(s.snapshots)
    counts.running = n(runningJobs(s))
    counts.models = n(modelTotal(s))
    counts.pending = n(s.pending_erasures)
    stageCounts.value = countStages(s)
    recentJobs.value = s.recent_jobs
    erasures.value = s.recent_erasures
  }
  catch (e) {
    error.value = e instanceof Error ? e : new Error(String(e))
  }
  finally {
    loading.value = false
  }
}

onMounted(load)
</script>
