<template>
  <div>
    <PageHeader :title="$t('dashboard.title')" :subtitle="$t('dashboard.subtitle')">
      <template #actions>
        <M3Button variant="tonal" icon="mdi-refresh" :loading="loading" :loading-label="$t('common.loading')" @click="load">
          {{ $t('common.refresh') }}
        </M3Button>
      </template>
    </PageHeader>

    <AppProblem :error="error" />

    <div class="grid grid-cols-[repeat(auto-fit,minmax(13rem,1fr))] gap-4">
      <M3Card v-for="tile in tiles" :key="tile.key" :to="tile.to" class="h-full">
        <div class="text-label-large text-on-surface-variant">{{ $t(`dashboard.${tile.key}`) }}</div>
        <div class="mt-1 text-headline-large">{{ tile.value }}</div>
      </M3Card>
    </div>

    <M3Card v-if="can('models.read')" class="mt-4" :title="$t('dashboard.modelsPerStage')">
      <div class="flex flex-wrap gap-3">
        <M3Chip
          v-for="s in STAGES"
          :key="s"
          :to="{ path: '/models', query: { stage: s } }"
          :tone="chipTone(stageColor(s))"
          :icon="stageIcon(s)"
        >
          {{ enumLabel('models.stage', s) }}: {{ stageCounts[s] ?? 0 }}
        </M3Chip>
      </div>
    </M3Card>

    <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
      <M3Card v-if="can('jobs.read')" :title="$t('dashboard.recentJobs')">
        <ul class="-mx-2">
          <li v-for="j in recentJobs" :key="j.id">
            <NuxtLink
              :to="`/jobs/${j.id}`"
              class="state-layer flex items-center justify-between gap-3 rounded-sm px-2 py-2 text-on-surface no-underline"
            >
              <span class="min-w-0">
                <span class="block truncate text-body-large">{{ enumLabel('jobs.type', j.type) }} · {{ shortId(j.id) }}</span>
                <span class="block text-body-medium text-on-surface-variant">{{ formatDateTime(j.created_at, locale) }}</span>
              </span>
              <StatusChip kind="job" :value="j.status" />
            </NuxtLink>
          </li>
        </ul>
        <M3EmptyState v-if="!recentJobs.length" :title="$t('common.empty')" />
      </M3Card>
      <M3Card v-if="can('players.manage')" :title="$t('dashboard.recentErasures')">
        <ul>
          <li v-for="e in erasures" :key="e.id" class="flex items-center justify-between gap-3 py-2">
            <span class="min-w-0">
              <span class="mono block truncate text-body-large">{{ e.pseudonym_prefix }}…</span>
              <span class="block text-body-medium text-on-surface-variant">{{ formatDateTime(e.requested_at, locale) }}</span>
            </span>
            <M3Chip small :tone="e.compaction_pending ? 'warning' : 'success'">
              {{ e.compaction_pending ? $t('dashboard.compactionPending') : $t('dashboard.compacted') }}
            </M3Chip>
          </li>
        </ul>
        <M3EmptyState v-if="!erasures.length" :title="$t('common.empty')" />
      </M3Card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { modelTotal, runningJobs, STAGES, stageCounts as countStages } from '~/lib/dashboard'
import { chipTone, formatDateTime, formatNumber, shortId, stageColor, stageIcon } from '~/lib/format'
import type { ErasureBrief, JobBrief } from '~/types'

const enumLabel = useEnumLabel()

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
