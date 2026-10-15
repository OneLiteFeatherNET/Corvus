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
          <v-card-subtitle class="text-wrap">{{ $t('players.localLogHint') }}</v-card-subtitle>
          <v-list bg-color="transparent">
            <v-list-item v-for="e in erasures" :key="e.id" :title="`${shortPseudonym(e.pseudonym)}…`" :subtitle="`${formatDateTime(e.requested_at, locale)} · ${e.requested_by}`">
              <template #append><v-chip size="small" label variant="tonal">{{ e.status }}</v-chip></template>
            </v-list-item>
            <v-list-item v-if="!erasures.length" :title="$t('common.empty')" />
          </v-list>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { countOf, modelsPerStage, recent, runningJobs, STAGES } from '~/lib/dashboard'
import { shortPseudonym } from '~/lib/erasure'
import { readErasureIds } from '~/lib/erasure-log'
import { countLabel, formatDateTime, shortId, stageColor, stageIcon } from '~/lib/format'
import type { Erasure, Job } from '~/types'

const { can } = useAuth()
const { locale } = useI18n()
const pools = usePools()
const snapshots = useSnapshots()
const jobs = useJobs()
const models = useModels()
const players = usePlayers()

const loading = ref(false)
const error = ref<Error | null>(null)
const counts = reactive<Record<string, string>>({ pools: '–', snapshots: '–', running: '–', models: '–' })
const stageCounts = ref<Record<string, number>>({})
const recentJobs = ref<Job[]>([])
const erasures = ref<Erasure[]>([])

interface Tile { key: string, to: string | { path: string, query: Record<string, string> }, value: string }
const tiles = computed(() => {
  const out: Tile[] = []
  if (can('pools.read')) out.push({ key: 'pools', to: '/pools', value: counts.pools ?? '–' })
  if (can('snapshots.read')) out.push({ key: 'snapshots', to: '/snapshots', value: counts.snapshots ?? '–' })
  if (can('jobs.read')) out.push({ key: 'runningJobs', to: { path: '/jobs', query: { status: 'running' } }, value: counts.running ?? '–' })
  if (can('models.read')) out.push({ key: 'models', to: '/models', value: counts.models ?? '–' })
  return out
})

async function load() {
  loading.value = true
  error.value = null
  const tasks: Array<Promise<void>> = []
  const guard = (p: Promise<void>) => tasks.push(p.catch((e) => { error.value = e instanceof Error ? e : new Error(String(e)) }))

  if (can('pools.read')) {
    guard(pools.list({ limit: 200 }).then((p) => {
      const c = countOf(p)
      counts.pools = countLabel(c.value, c.partial)
    }))
  }
  if (can('snapshots.read')) {
    guard(snapshots.list({ limit: 200 }).then((p) => {
      const c = countOf(p)
      counts.snapshots = countLabel(c.value, c.partial)
    }))
  }
  if (can('jobs.read')) {
    guard(jobs.list({ limit: 200 }).then((p) => {
      counts.running = countLabel(runningJobs(p.items), p.next_cursor !== null)
      recentJobs.value = recent(p.items)
    }))
  }
  if (can('models.read')) {
    guard(models.list({ limit: 200 }).then((p) => {
      const c = countOf(p)
      counts.models = countLabel(c.value, c.partial)
      stageCounts.value = modelsPerStage(p.items)
    }))
  }
  if (can('players.manage')) {
    const ids = readErasureIds(localStorage).slice(0, 5)
    guard(Promise.all(ids.map(id => players.erasure(id).catch(() => null))).then((rows) => {
      erasures.value = rows.filter((r): r is Erasure => r !== null)
    }))
  }
  await Promise.all(tasks)
  loading.value = false
}

onMounted(load)
</script>
