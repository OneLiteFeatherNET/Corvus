<template>
  <div>
    <PageHeader :title="$t('pools.title')">
      <template #actions>
        <v-text-field
          v-model="search"
          :label="$t('common.search')"
          prepend-inner-icon="mdi-magnify"
          density="compact"
          hide-details
          clearable
          style="min-width: 240px"
        />
        <v-btn v-if="can('pools.write')" to="/pools/new" prepend-icon="mdi-plus">{{ $t('pools.new') }}</v-btn>
      </template>
    </PageHeader>

    <AppProblem :error="list.error.value" />
    <v-data-table
      :headers="headers"
      :items="rows"
      :loading="list.loading.value"
      :no-data-text="$t('common.empty')"
      item-value="id"
      hover
    >
      <template #[`item.name`]="{ item }">
        <NuxtLink :to="`/pools/${item.id}`" class="text-primary font-weight-medium">{{ item.name }}</NuxtLink>
      </template>
      <template #[`item.filter`]="{ item }">
        <div class="d-flex flex-wrap ga-1">
          <v-chip v-for="c in summarizeFilter(item.filter)" :key="c.key" size="x-small" label variant="tonal">
            {{ $t(`pools.summary.${c.key}`) }}: {{ c.value }}
          </v-chip>
        </div>
      </template>
      <template #[`item.updated_at`]="{ item }">{{ formatDateTime(item.updated_at, locale) }}</template>
    </v-data-table>
    <div class="mt-4">
      <v-btn v-if="list.hasMore()" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>
    </div>
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
