<template>
  <header class="d-flex flex-wrap align-center ga-3 mb-6">
    <div>
      <h1 class="text-headline-large">{{ title }}</h1>
      <p v-if="subtitle" class="text-body-large text-on-surface-variant">{{ subtitle }}</p>
    </div>
    <v-spacer />
    <div class="d-flex flex-wrap align-center ga-2">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title: string, subtitle?: string }>()
</script>
