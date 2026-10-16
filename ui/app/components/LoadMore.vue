<template>
  <div v-if="visible" class="mt-4 flex justify-center">
    <M3Button variant="tonal" :loading="loading" :loading-label="$t('common.loading')" @click="$emit('more')">
      {{ $t('common.loadMore') }}
    </M3Button>
  </div>
</template>

<script setup lang="ts">
defineProps<{ visible: boolean, loading?: boolean }>()
defineEmits<{ more: [] }>()
</script>
