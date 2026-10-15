<template>
  <div>
    <PageHeader :title="$t('snapshots.title')" />
    <SnapshotTable :items="list.items.value" :loading="list.loading.value" :error="list.error.value" />
    <v-btn v-if="list.hasMore()" class="mt-3" variant="tonal" :loading="list.loading.value" @click="list.loadMore()">{{ $t('common.loadMore') }}</v-btn>
  </div>
</template>

<script setup lang="ts">
const snapshots = useSnapshots()
const list = usePagedList(cursor => snapshots.list({ limit: 50, cursor }))
onMounted(list.load)
</script>
