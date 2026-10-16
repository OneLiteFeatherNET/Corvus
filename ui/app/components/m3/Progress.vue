<template>
  <div v-if="circular" role="progressbar" :aria-label="label" class="inline-block size-10 animate-spin text-primary">
    <svg viewBox="0 0 48 48" class="size-full" aria-hidden="true">
      <circle cx="24" cy="24" r="18" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-dasharray="85 30" />
    </svg>
  </div>
  <div
    v-else
    role="progressbar"
    :aria-label="label"
    class="relative block h-1 w-full overflow-hidden rounded-full bg-secondary-container"
  >
    <div class="m3-progress-bar absolute inset-y-0 w-2/5 rounded-full bg-primary" />
  </div>
</template>

<script setup lang="ts">
/** Indeterminate progress indicator, linear (default) or circular. `label` names it for assistive technology. */
withDefaults(defineProps<{ label: string, circular?: boolean }>(), { circular: false })
</script>

<style scoped>
.m3-progress-bar {
  animation: m3-progress 1.4s var(--ease-emphasized) infinite;
}

@keyframes m3-progress {
  from { left: -40%; }
  to { left: 100%; }
}
</style>
