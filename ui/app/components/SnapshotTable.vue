<template>
  <div>
    <AppProblem :error="error ?? null" />
    <M3DataTable :columns="headers" :items="items" :loading="loading">
      <template #[`item.id`]="{ item }">
        <NuxtLink :to="`/snapshots/${item.id}`" class="mono font-medium">{{ shortId(item.id) }}</NuxtLink>
      </template>
      <template #[`item.pool_name`]="{ item }">
        <NuxtLink :to="`/pools/${item.pool_id}`">{{ item.pool_name }}</NuxtLink>
      </template>
      <template #[`item.sha256`]="{ item }"><span class="mono">{{ item.sha256.slice(0, 12) }}…</span></template>
      <template #[`item.created_at`]="{ item }">{{ formatDateTime(item.created_at, locale) }}</template>
    </M3DataTable>
  </div>
</template>

<script setup lang="ts">
import { formatDateTime, shortId } from '~/lib/format'
import type { Snapshot } from '~/types'

defineProps<{ items: Snapshot[], loading?: boolean, error?: Error | null }>()
const { t, locale } = useI18n()
const headers = computed(() => [
  { title: 'ID', key: 'id' },
  { title: t('snapshots.pool'), key: 'pool_name' },
  { title: t('snapshots.manifestHash'), key: 'sha256' },
  { title: t('snapshots.sessions'), key: 'session_count', align: 'end' as const },
  { title: t('snapshots.files'), key: 'file_count', align: 'end' as const },
  { title: t('common.created'), key: 'created_at' },
])
</script>
