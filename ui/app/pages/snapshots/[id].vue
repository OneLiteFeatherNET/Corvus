<template>
  <div>
    <AppProblem :error="loadError" />
    <M3Progress v-if="!snapshot && !loadError" :label="$t('common.loading')" />
    <template v-if="snapshot">
      <PageHeader :title="`${$t('snapshots.detail')} ${shortId(snapshot.id)}`" :subtitle="snapshot.pool_name">
        <template #actions>
          <M3Button v-if="can('jobs.create')" icon="mdi-play-circle-outline" @click="jobDialog = true">{{ $t('snapshots.startJob') }}</M3Button>
          <M3Button variant="tonal" :to="`/pools/${snapshot.pool_id}`" icon="mdi-database-search-outline">{{ $t('snapshots.openPool') }}</M3Button>
        </template>
      </PageHeader>

      <M3Card class="mb-4">
        <dl class="kv">
          <dt>ID</dt><dd class="mono">{{ snapshot.id }}</dd>
          <dt>{{ $t('snapshots.manifestHash') }}</dt><dd class="mono">{{ snapshot.sha256 }}</dd>
          <dt>{{ $t('snapshots.sessions') }}</dt><dd>{{ snapshot.session_count }}</dd>
          <dt>{{ $t('snapshots.files') }}</dt><dd>{{ snapshot.file_count }}</dd>
          <dt>{{ $t('common.created') }}</dt><dd>{{ formatDateTime(snapshot.created_at, locale) }} · {{ snapshot.created_by }}</dd>
        </dl>
      </M3Card>

      <M3Card :title="$t('snapshots.filter')">
        <pre class="mono whitespace-pre-wrap">{{ JSON.stringify(manifestFilter, null, 2) }}</pre>
      </M3Card>

      <M3Dialog v-model="jobDialog" :title="$t('snapshots.startJob')" @after-leave="create.reset()">
        <div class="flex flex-col gap-4 pt-1">
          <AppProblem :error="create.error.value" />
          <M3Select v-model="jobType" :items="jobTypes" :label="$t('jobs.typeLabel')" />
          <M3TextField
            v-model="paramsText"
            multiline
            :rows="5"
            :label="$t('jobs.params')"
            :hint="$t('jobs.paramsHint')"
            :error="paramsError"
            input-class="mono"
          />
        </div>
        <template #actions>
          <M3Button variant="text" @click="jobDialog = false">{{ $t('common.cancel') }}</M3Button>
          <M3Button :loading="create.pending.value" :loading-label="$t('common.loading')" @click="submit">{{ $t('snapshots.startJob') }}</M3Button>
        </template>
      </M3Dialog>
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
