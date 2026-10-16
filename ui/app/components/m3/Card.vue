<template>
  <component
    :is="link ?? 'section'"
    :to="to"
    :class="[
      'block min-w-0 rounded-md p-4 text-on-surface no-underline',
      VARIANTS[variant],
      to ? 'state-layer' : '',
    ]"
  >
    <h2 v-if="title" class="mb-3 text-title-large">{{ title }}</h2>
    <slot />
  </component>
</template>

<script setup lang="ts">
import { computed, resolveComponent } from 'vue'

const props = withDefaults(defineProps<{
  variant?: 'filled' | 'elevated' | 'outlined'
  title?: string
  to?: string | Record<string, unknown>
}>(), { variant: 'filled', title: '', to: undefined })

// Resolved only for links, so the primitive also mounts without the router (unit tests).
const link = computed(() => (props.to ? resolveComponent('NuxtLink') : null))
const VARIANTS = {
  filled: 'bg-surface-container',
  elevated: 'bg-surface-container-low shadow-elevation-1',
  outlined: 'border border-outline-variant bg-surface',
}
</script>
