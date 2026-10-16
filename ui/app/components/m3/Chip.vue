<template>
  <component
    :is="link ?? 'span'"
    :to="to"
    :class="[
      'state-layer inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-sm no-underline',
      small ? 'h-6 px-2 text-label-medium' : 'h-8 px-3 text-label-large',
      TONES[tone],
    ]"
  >
    <M3Icon v-if="icon" :name="icon" :size="small ? 14 : 18" />
    <span class="min-w-0 truncate"><slot /></span>
    <button
      v-if="removable"
      type="button"
      class="state-layer -mr-1 inline-flex size-5 items-center justify-center rounded-full"
      :aria-label="removeLabel"
      @click.stop.prevent="$emit('remove')"
    >
      <M3Icon name="mdi-close" :size="14" />
    </button>
  </component>
</template>

<script setup lang="ts">
import { computed, resolveComponent } from 'vue'
import M3Icon from './Icon.vue'

const props = withDefaults(defineProps<{
  tone?: 'neutral' | 'primary' | 'secondary' | 'tertiary' | 'error' | 'success' | 'warning'
  icon?: string
  small?: boolean
  to?: string | Record<string, unknown>
  removable?: boolean
  removeLabel?: string
}>(), { tone: 'neutral', icon: undefined, to: undefined, removeLabel: '' })
defineEmits<{ remove: [] }>()

// Resolved only for links, so the primitive also mounts without the router (unit tests).
const link = computed(() => (props.to ? resolveComponent('NuxtLink') : null))
const TONES = {
  neutral: 'border border-outline-variant text-on-surface-variant',
  primary: 'bg-primary-container text-on-primary-container',
  secondary: 'bg-secondary-container text-on-secondary-container',
  tertiary: 'bg-tertiary-container text-on-tertiary-container',
  error: 'bg-error-container text-on-error-container',
  success: 'bg-success-container text-on-success-container',
  warning: 'bg-warning-container text-on-warning-container',
}
</script>
