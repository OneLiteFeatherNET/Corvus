<template>
  <div>
    <AppProblem :error="loadError" />
    <M3Progress v-if="!job && !loadError" :label="$t('common.loading')" />
    <template v-if="job">
      <PageHeader :title="`${enumLabel('jobs.type', job.type)} ${shortId(job.id)}`">
        <template #actions>
          <StatusChip kind="job" :value="job.status" />
          <M3Button variant="tonal" icon="mdi-refresh" :loading="refreshing" :loading-label="$t('common.loading')" @click="refresh">{{ $t('common.refresh') }}</M3Button>
        </template>
      </PageHeader>

      <div aria-live="polite" class="mb-2">
        <M3Alert v-if="polling.poller.running" tone="info" class="mb-4">{{ $t('jobs.polling') }}</M3Alert>
        <AppProblem :error="polling.lastError.value" />
      </div>

      <M3Card class="mb-4">
        <dl class="kv">
          <dt>ID</dt><dd class="mono">{{ job.id }}</dd>
          <dt>{{ $t('jobs.snapshot') }}</dt><dd><NuxtLink :to="`/snapshots/${job.snapshot_id}`" class="mono">{{ job.snapshot_id }}</NuxtLink></dd>
          <dt>{{ $t('jobs.runner') }}</dt><dd>{{ job.runner }}</dd>
          <dt>{{ $t('common.created') }}</dt><dd>{{ formatDateTime(job.created_at, locale) }} · {{ job.created_by }}</dd>
          <dt>{{ $t('common.updated') }}</dt><dd>{{ formatDateTime(job.updated_at, locale) }}</dd>
          <dt>{{ $t('jobs.finished') }}</dt><dd>{{ formatDateTime(job.finished_at, locale) }}</dd>
          <dt>{{ $t('jobs.message') }}</dt><dd>{{ job.message || '–' }}</dd>
        </dl>
      </M3Card>

      <M3Card :title="$t('jobs.params')">
        <pre class="mono whitespace-pre-wrap">{{ JSON.stringify(job.params, null, 2) }}</pre>
      </M3Card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import type { Job } from '~/types'

const enumLabel = useEnumLabel()

const route = useRoute()
const { locale } = useI18n()
const jobs = useJobs()
const id = String(route.params.id)

const job = ref<Job | null>(null)
const loadError = ref<Error | null>(null)
const refreshing = ref(false)
const polling = useJobPolling(job, jobs.get)

async function refresh() {
  refreshing.value = true
  try {
    job.value = await jobs.get(id)
    polling.sync()
  }
  catch (e) {
    loadError.value = e instanceof Error ? e : new Error(String(e))
  }
  finally {
    refreshing.value = false
  }
}

onMounted(refresh)
</script>
