<template>
  <div>
    <PageHeader :title="$t('pools.edit')" />
    <AppProblem :error="error" />
    <v-progress-linear v-if="!pool && !error" indeterminate :aria-label="$t('common.loading')" />
    <PoolEditor v-if="pool" :pool="pool" />
  </div>
</template>

<script setup lang="ts">
import type { Pool } from '~/types'

const route = useRoute()
const pools = usePools()
const pool = ref<Pool>()
const error = ref<Error | null>(null)

onMounted(async () => {
  try {
    pool.value = await pools.get(String(route.params.id))
  }
  catch (e) {
    error.value = e instanceof Error ? e : new Error(String(e))
  }
})
</script>
