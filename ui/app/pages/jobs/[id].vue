<template>
  <div>
    <AppProblem :error="loadError" />
    <v-progress-linear v-if="!job && !loadError" indeterminate :aria-label="$t('common.loading')" />
    <template v-if="job">
      <PageHeader :title="`${$t(`jobs.type.${job.type}`)} ${shortId(job.id)}`">
        <template #actions>
          <StatusChip kind="job" :value="job.status" />
          <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="refreshing" @click="refresh">{{ $t('common.refresh') }}</v-btn>
        </template>
      </PageHeader>

      <div aria-live="polite" class="mb-2">
        <v-alert v-if="polling.poller.running" type="info" variant="tonal" density="compact" icon="mdi-progress-clock">{{ $t('jobs.polling') }}</v-alert>
        <AppProblem :error="polling.lastError.value" />
      </div>

      <v-card variant="flat" color="surface-container-low" class="mb-4">
        <v-card-text>
          <dl class="kv">
            <dt>ID</dt><dd class="mono">{{ job.id }}</dd>
            <dt>{{ $t('jobs.snapshot') }}</dt><dd><NuxtLink :to="`/snapshots/${job.snapshot_id}`" class="mono">{{ job.snapshot_id }}</NuxtLink></dd>
            <dt>{{ $t('jobs.runner') }}</dt><dd>{{ job.runner }}</dd>
            <dt>{{ $t('common.created') }}</dt><dd>{{ formatDateTime(job.created_at, locale) }} · {{ job.created_by }}</dd>
            <dt>{{ $t('common.updated') }}</dt><dd>{{ formatDateTime(job.updated_at, locale) }}</dd>
            <dt>{{ $t('jobs.finished') }}</dt><dd>{{ formatDateTime(job.finished_at, locale) }}</dd>
            <dt>{{ $t('jobs.message') }}</dt><dd>{{ job.message || '–' }}</dd>
          </dl>
        </v-card-text>
      </v-card>

      <v-card variant="flat" color="surface-container-low">
        <v-card-title class="text-title-large">{{ $t('jobs.params') }}</v-card-title>
        <v-card-text><pre class="mono" style="white-space: pre-wrap">{{ JSON.stringify(job.params, null, 2) }}</pre></v-card-text>
      </v-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import type { Job } from '~/types'

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
