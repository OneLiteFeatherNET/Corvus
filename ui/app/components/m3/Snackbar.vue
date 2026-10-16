<template>
  <div class="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex justify-center px-4" role="status" aria-live="polite">
    <div
      v-if="message"
      class="pointer-events-auto flex min-h-12 w-full max-w-[36rem] items-center gap-2 rounded-xs bg-inverse-surface py-1 pr-1 pl-4 text-body-medium text-inverse-on-surface shadow-elevation-3"
    >
      <span class="min-w-0 flex-1 py-2">{{ message }}</span>
      <button
        type="button"
        class="state-layer inline-flex h-10 items-center rounded-full px-3 text-label-large text-inverse-primary"
        @click="$emit('dismiss')"
      >
        {{ closeLabel }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/** Transient message. The parent owns the timer (see useSnackbar) and clears `message` on dismiss. */
defineProps<{ message: string, closeLabel: string }>()
defineEmits<{ dismiss: [] }>()
</script>
