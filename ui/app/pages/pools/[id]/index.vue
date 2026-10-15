<template>
  <div>
    <AppProblem :error="loadError" />
    <v-progress-linear v-if="!pool && !loadError" indeterminate :aria-label="$t('common.loading')" />
    <template v-if="pool">
      <PageHeader :title="pool.name" :subtitle="pool.description">
        <template #actions>
          <v-btn v-if="can('snapshots.create')" prepend-icon="mdi-camera-plus-outline" :loading="snapshot.pending.value" @click="snapshot.run()">
            {{ $t('pools.createSnapshot') }}
          </v-btn>
          <v-btn v-if="can('pools.write')" variant="tonal" :to="`/pools/${pool.id}/edit`" prepend-icon="mdi-pencil-outline">{{ $t('common.edit') }}</v-btn>
          <v-btn v-if="can('pools.write')" variant="text" color="error" prepend-icon="mdi-delete-outline" @click="confirmDelete = true">{{ $t('common.delete') }}</v-btn>
        </template>
      </PageHeader>

      <AppProblem :error="snapshot.error.value" />
      <v-alert v-if="created" type="success" variant="tonal" class="mb-4" role="status">
        {{ $t('pools.snapshotCreated') }}
        <NuxtLink :to="`/snapshots/${created.id}`" class="font-weight-medium">{{ shortId(created.id) }}</NuxtLink>
      </v-alert>

      <v-row>
        <v-col cols="12" lg="6">
          <v-card variant="flat" color="surface-container-low">
            <v-card-title class="text-title-large">{{ $t('pools.form.filter') }}</v-card-title>
            <v-card-text>
              <div class="d-flex flex-wrap ga-1 mb-3">
                <v-chip v-for="c in summary" :key="c.key" label variant="tonal">{{ $t(`pools.summary.${c.key}`) }}: {{ c.value }}</v-chip>
                <span v-if="!summary.length" class="text-on-surface-variant">{{ $t('pools.noFilter') }}</span>
              </div>
              <p class="text-body-medium">{{ $t('pools.form.excludeOrphans') }}: {{ pool.filter.exclude_orphans ? $t('common.yes') : $t('common.no') }}</p>
              <details class="mt-3">
                <summary class="text-label-large" style="cursor: pointer">{{ $t('pools.rawFilter') }}</summary>
                <pre class="mono mt-2" style="white-space: pre-wrap">{{ JSON.stringify(pool.filter, null, 2) }}</pre>
              </details>
            </v-card-text>
          </v-card>
        </v-col>
        <v-col cols="12" lg="6">
          <PreviewPanel :preview="preview" :loading="previewAction.pending.value" :error="previewAction.error.value" @refresh="previewAction.run()" />
        </v-col>
      </v-row>

      <h2 class="text-headline-small mt-8 mb-3">{{ $t('nav.snapshots') }}</h2>
      <SnapshotTable :items="snapshots.items.value" :loading="snapshots.loading.value" :error="snapshots.error.value" />
      <v-btn v-if="snapshots.hasMore()" class="mt-3" variant="tonal" @click="snapshots.loadMore()">{{ $t('common.loadMore') }}</v-btn>

      <v-dialog v-model="confirmDelete" max-width="480">
        <v-card :title="$t('pools.deleteTitle')">
          <v-card-text>{{ $t('pools.deleteText', { name: pool.name }) }}</v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="confirmDelete = false">{{ $t('common.cancel') }}</v-btn>
            <v-btn color="error" :loading="remove.pending.value" @click="remove.run()">{{ $t('common.delete') }}</v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
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
