<template>
  <div>
    <AppProblem :error="loadError" />
    <M3Progress v-if="!pool && !loadError" :label="$t('common.loading')" />
    <template v-if="pool">
      <PageHeader :title="pool.name" :subtitle="pool.description">
        <template #actions>
          <M3Button v-if="can('snapshots.create')" icon="mdi-camera-plus-outline" :loading="snapshot.pending.value" :loading-label="$t('common.loading')" @click="snapshot.run()">
            {{ $t('pools.createSnapshot') }}
          </M3Button>
          <M3Button v-if="can('pools.write')" variant="tonal" :to="`/pools/${pool.id}/edit`" icon="mdi-pencil-outline">{{ $t('common.edit') }}</M3Button>
          <M3Button v-if="can('pools.write')" variant="text" tone="error" icon="mdi-delete-outline" @click="confirmDelete = true">{{ $t('common.delete') }}</M3Button>
        </template>
      </PageHeader>

      <AppProblem :error="snapshot.error.value" />
      <M3Alert v-if="created" tone="success" class="mb-4">
        {{ $t('pools.snapshotCreated') }}
        <NuxtLink :to="`/snapshots/${created.id}`" class="font-medium">{{ shortId(created.id) }}</NuxtLink>
      </M3Alert>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <M3Card :title="$t('pools.form.filter')">
          <div class="mb-3 flex flex-wrap gap-1">
            <M3Chip v-for="c in summary" :key="c.key">{{ $t(`pools.summary.${c.key}`) }}: {{ c.value }}</M3Chip>
            <span v-if="!summary.length" class="text-on-surface-variant">{{ $t('pools.noFilter') }}</span>
          </div>
          <p class="text-body-medium">{{ $t('pools.form.excludeOrphans') }}: {{ pool.filter.exclude_orphans ? $t('common.yes') : $t('common.no') }}</p>
          <details class="mt-3">
            <summary class="cursor-pointer text-label-large">{{ $t('pools.rawFilter') }}</summary>
            <pre class="mono mt-2 whitespace-pre-wrap">{{ JSON.stringify(pool.filter, null, 2) }}</pre>
          </details>
        </M3Card>
        <PreviewPanel :preview="preview" :loading="previewAction.pending.value" :error="previewAction.error.value" @refresh="previewAction.run()" />
      </div>

      <h2 class="mt-8 mb-3 text-headline-small">{{ $t('nav.snapshots') }}</h2>
      <SnapshotTable :items="snapshots.items.value" :loading="snapshots.loading.value" :error="snapshots.error.value" />
      <LoadMore :visible="snapshots.hasMore()" :loading="snapshots.loading.value" @more="snapshots.loadMore()" />

      <M3Dialog v-model="confirmDelete" :title="$t('pools.deleteTitle')" width="sm" alert>
        {{ $t('pools.deleteText', { name: pool.name }) }}
        <template #actions>
          <M3Button variant="text" @click="confirmDelete = false">{{ $t('common.cancel') }}</M3Button>
          <M3Button tone="error" :loading="remove.pending.value" :loading-label="$t('common.loading')" @click="remove.run()">{{ $t('common.delete') }}</M3Button>
        </template>
      </M3Dialog>
    </template>
  </div>
</template>

<script setup lang="ts">
import { shortId } from '~/lib/format'
import { summarizeFilter } from '~/lib/pool-filter'
import type { Pool, Preview, Snapshot } from '~/types'

const route = useRoute()
const router = useRouter()
const { can } = useAuth()
const pools = usePools()
const id = String(route.params.id)

const pool = ref<Pool>()
const loadError = ref<Error | null>(null)
const preview = ref<Preview | null>(null)
const created = ref<Snapshot | null>(null)
const confirmDelete = ref(false)

const summary = computed(() => (pool.value ? summarizeFilter(pool.value.filter) : []))
const snapshots = usePagedList(cursor => pools.snapshots(id, { limit: 50, cursor }))
const previewAction = useAction(async () => {
  preview.value = await pools.preview(id)
})
const snapshot = useAction(async () => {
  created.value = await pools.createSnapshot(id)
  await snapshots.load()
})
const remove = useAction(async () => {
  await pools.remove(id)
  await router.replace('/pools')
})

onMounted(async () => {
  try {
    pool.value = await pools.get(id)
  }
  catch (e) {
    loadError.value = e instanceof Error ? e : new Error(String(e))
    return
  }
  void previewAction.run()
  void snapshots.load()
})
</script>
