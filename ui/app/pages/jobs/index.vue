<template>
  <div>
    <PageHeader :title="$t('jobs.title')">
      <template #actions>
        <v-select
          v-model="status"
          :items="statusItems"
          :label="$t('jobs.statusLabel')"
          density="compact"
          hide-details
          style="min-width: 200px"
        />
        <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="list.loading.value" @click="list.load()">{{ $t('common.refresh') }}</v-btn>
      </template>
    </PageHeader>
    <AppProblem :error="list.error.value" />
    <v-data-table :headers="headers" :items="list.items.value" :loading="list.loading.value" :no-data-text="$t('common.empty')" item-value="id" hover>
      <template #[`item.id`]="{ item }">
        <NuxtLink :to="`/jobs/${item.id}`" class="text-primary font-weight-medium mono">{{ shortId(item.id) }}</NuxtLink>
      </template>
      <template #[`item.type`]="{ item }">{{ $t(`jobs.type.${item.type}`) }}</template>
      <template #[`item.status`]="{ item }"><StatusChip kind="job" :value="item.status" /></template>
      <template #[`item.snapshot_id`]="{ item }">
        <NuxtLink :to="`/snapshots/${item.snapshot_id}`" class="mono">{{ shortId(item.snapshot_id) }}</NuxtLink>
      </template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </v-data-table>
    <v-btn v-if="list.hasMore()" class="mt-3" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import type { JobState } from '~/types'

const { t, locale } = useI18n()
const jobs = useJobs()
const status = useQueryState('status')

const list = usePagedList(cursor => jobs.list({ limit: 50, cursor, status: (status.value || undefined) as JobState | undefined }))
onMounted(list.load)
watch(status, () => list.load())

const statusItems = computed(() => [
  { value: '', title: t('common.all') },
  ...(['queued', 'running', 'succeeded', 'failed'] as const).map(v => ({ value: v, title: t(`jobs.status.${v}`) })),
])
const headers = computed(() => [
  { title: 'ID', key: 'id' },
  { title: t('jobs.typeLabel'), key: 'type' },
  { title: t('jobs.statusLabel'), key: 'status' },
  { title: t('jobs.snapshot'), key: 'snapshot_id' },
  { title: t('common.created'), key: 'created_at' },
  { title: t('common.createdBy'), key: 'created_by' },
])
</script>
