<template>
  <div>
    <PageHeader :title="$t('jobs.title')" />
    <div class="mb-4 flex flex-wrap items-start gap-3">
      <M3Select v-model="status" :items="statusItems" :label="$t('jobs.statusLabel')" class="w-full sm:w-56" />
      <M3Button class="mt-2 sm:ml-auto" variant="tonal" icon="mdi-refresh" :loading="list.loading.value" :loading-label="$t('common.loading')" @click="list.load()">
        {{ $t('common.refresh') }}
      </M3Button>
    </div>
    <AppProblem :error="list.error.value" />
    <M3DataTable :columns="headers" :items="list.items.value" :loading="list.loading.value">
      <template #[`item.id`]="{ item }">
        <NuxtLink :to="`/jobs/${item.id}`" class="mono font-medium">{{ shortId(item.id) }}</NuxtLink>
      </template>
      <template #[`item.type`]="{ item }">{{ enumLabel('jobs.type', item.type) }}</template>
      <template #[`item.status`]="{ item }"><StatusChip kind="job" :value="item.status" /></template>
      <template #[`item.snapshot_id`]="{ item }">
        <NuxtLink :to="`/snapshots/${item.snapshot_id}`" class="mono">{{ shortId(item.snapshot_id) }}</NuxtLink>
      </template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import type { JobState } from '~/types'

const enumLabel = useEnumLabel()

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
