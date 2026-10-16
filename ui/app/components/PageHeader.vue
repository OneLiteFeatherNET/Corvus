<template>
  <header class="mb-6 flex flex-wrap items-end gap-x-4 gap-y-3">
    <div class="min-w-0 flex-1 basis-64">
      <h1 class="text-headline-large break-words text-on-surface">{{ title }}</h1>
      <p v-if="subtitle" class="mt-1 text-body-large text-on-surface-variant">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title: string, subtitle?: string }>()
</script>
