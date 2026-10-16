<template>
  <div>
    <PageHeader :title="$t('pools.title')" />

    <div class="mb-4 flex flex-wrap items-start gap-3">
      <M3TextField
        v-model="search"
        :label="$t('common.search')"
        leading-icon="mdi-magnify"
        clearable
        :clear-label="$t('common.delete')"
        class="w-full sm:w-72"
      />
      <M3Button v-if="can('pools.write')" class="mt-2 sm:ml-auto" to="/pools/new" icon="mdi-plus">{{ $t('pools.new') }}</M3Button>
    </div>

    <AppProblem :error="list.error.value" />
    <M3DataTable :columns="headers" :items="rows" :loading="list.loading.value">
      <template #[`item.name`]="{ item }">
        <NuxtLink :to="`/pools/${item.id}`" class="font-medium">{{ item.name }}</NuxtLink>
      </template>
      <template #[`item.filter`]="{ item }">
        <div class="flex flex-wrap gap-1">
          <M3Chip v-for="c in summarizeFilter(item.filter)" :key="c.key" small>
            {{ $t(`pools.summary.${c.key}`) }}: {{ c.value }}
          </M3Chip>
        </div>
      </template>
      <template #[`item.updated_at`]="{ item }">{{ formatDateTime(item.updated_at, locale) }}</template>
    </M3DataTable>
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />
  </div>
</template>

<script setup lang="ts">
import { formatDateTime } from '~/lib/format'
import { summarizeFilter } from '~/lib/pool-filter'

const { can } = useAuth()
const { t, locale } = useI18n()
const pools = usePools()
const search = useQueryState('q')

const list = usePagedList(cursor => pools.list({ limit: 50, cursor }))
onMounted(list.load)

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? list.items.value.filter(p => p.name.toLowerCase().includes(q)) : list.items.value
})
const headers = computed(() => [
  { title: t('pools.form.name'), key: 'name' },
  { title: t('pools.form.filter'), key: 'filter', sortable: false },
  { title: t('common.updated'), key: 'updated_at' },
  { title: t('common.createdBy'), key: 'created_by' },
])
</script>
