<template>
  <div>
    <PageHeader :title="$t('snapshots.title')" />
    <SnapshotTable :items="list.items.value" :loading="list.loading.value" :error="list.error.value" />
    <LoadMore :visible="list.hasMore()" :loading="list.loading.value" @more="list.loadMore()" />
  </div>
</template>

<script setup lang="ts">
const snapshots = useSnapshots()
const list = usePagedList(cursor => snapshots.list({ limit: 50, cursor }))
onMounted(list.load)
</script>
