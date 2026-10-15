<template>
  <div>
    <AppProblem :error="loadError" />
    <v-progress-linear v-if="!snapshot && !loadError" indeterminate :aria-label="$t('common.loading')" />
    <template v-if="snapshot">
      <PageHeader :title="`${$t('snapshots.detail')} ${shortId(snapshot.id)}`" :subtitle="snapshot.pool_name">
        <template #actions>
          <v-btn v-if="can('jobs.create')" prepend-icon="mdi-play-circle-outline" @click="jobDialog = true">{{ $t('snapshots.startJob') }}</v-btn>
          <v-btn variant="tonal" :to="`/pools/${snapshot.pool_id}`" prepend-icon="mdi-database-search-outline">{{ $t('snapshots.openPool') }}</v-btn>
        </template>
      </PageHeader>

      <v-card variant="flat" color="surface-container-low" class="mb-4">
        <v-card-text>
          <dl class="kv">
            <dt>ID</dt><dd class="mono">{{ snapshot.id }}</dd>
            <dt>{{ $t('snapshots.manifestHash') }}</dt><dd class="mono">{{ snapshot.sha256 }}</dd>
            <dt>{{ $t('snapshots.sessions') }}</dt><dd>{{ snapshot.session_count }}</dd>
            <dt>{{ $t('snapshots.files') }}</dt><dd>{{ snapshot.file_count }}</dd>
            <dt>{{ $t('common.created') }}</dt><dd>{{ formatDateTime(snapshot.created_at, locale) }} · {{ snapshot.created_by }}</dd>
          </dl>
        </v-card-text>
      </v-card>

      <v-card variant="flat" color="surface-container-low">
        <v-card-title class="text-title-large">{{ $t('snapshots.filter') }}</v-card-title>
        <v-card-text><pre class="mono" style="white-space: pre-wrap">{{ JSON.stringify(manifestFilter, null, 2) }}</pre></v-card-text>
      </v-card>

      <v-dialog v-model="jobDialog" max-width="560" @after-leave="create.reset()">
        <v-card :title="$t('snapshots.startJob')">
          <v-card-text>
            <AppProblem :error="create.error.value" />
            <v-select v-model="jobType" :items="jobTypes" :label="$t('jobs.typeLabel')" />
            <v-textarea
              v-model="paramsText"
              :label="$t('jobs.params')"
              :hint="$t('jobs.paramsHint')"
              persistent-hint
              rows="5"
              class="mono"
              :error-messages="paramsError"
            />
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="jobDialog = false">{{ $t('common.cancel') }}</v-btn>
            <v-btn :loading="create.pending.value" @click="submit">{{ $t('snapshots.startJob') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </template>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import { parseJobParams } from '~/lib/job-params'
import type { JobType, Snapshot } from '~/types'

const route = useRoute()
const router = useRouter()
const { can } = useAuth()
const { t, locale } = useI18n()
const snapshots = useSnapshots()
const jobs = useJobs()
const id = String(route.params.id)

const snapshot = ref<Snapshot>()
const loadError = ref<Error | null>(null)
const jobDialog = ref(false)
const jobType = ref<JobType>('train')
const paramsText = ref('')
const paramsError = ref('')
const jobTypes = computed(() => (['build_features', 'evaluate', 'train'] as const).map(v => ({ value: v, title: t(`jobs.type.${v}`) })))
const manifestFilter = computed(() => (snapshot.value?.manifest as { filter?: unknown } | null | undefined)?.filter ?? {})

const create = useAction(async () => {
  const parsed = parseJobParams(paramsText.value)
  if (!parsed.ok) {
    paramsError.value = t(`jobs.paramsErrors.${parsed.error}`)
    return
  }
  paramsError.value = ''
  const job = await jobs.create({ type: jobType.value, snapshot_id: id, params: parsed.value })
  await router.push(`/jobs/${job.id}`)
})
const submit = () => create.run()

onMounted(async () => {
  try {
    snapshot.value = await snapshots.get(id)
  }
  catch (e) {
    loadError.value = e instanceof Error ? e : new Error(String(e))
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
