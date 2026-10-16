<template>
  <component
    :is="link ?? 'button'"
    v-bind="$attrs"
    :to="to"
    :type="to ? undefined : 'button'"
    :disabled="!to && disabled ? true : undefined"
    :aria-label="label"
    :aria-pressed="pressed === undefined ? undefined : String(pressed)"
    :class="[
      'state-layer inline-flex size-10 shrink-0 items-center justify-center rounded-full no-underline',
      disabled ? 'text-on-surface/38' : VARIANTS[variant],
    ]"
  >
    <M3Icon :name="icon" :size="size" />
  </component>
</template>

<script setup lang="ts">
import { computed, resolveComponent } from 'vue'
import M3Icon from './Icon.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  icon: string
  /** Accessible name; an icon button has no visible text. */
  label: string
  variant?: 'standard' | 'filled' | 'tonal' | 'outlined'
  to?: string | Record<string, unknown>
  disabled?: boolean
  pressed?: boolean
  size?: number
}>(), { variant: 'standard', to: undefined, pressed: undefined, size: 24 })

// Resolved only for links, so the primitive also mounts without the router (unit tests).
const link = computed(() => (props.to ? resolveComponent('NuxtLink') : null))
const VARIANTS = {
  standard: 'text-on-surface-variant',
  filled: 'bg-primary text-on-primary',
  tonal: 'bg-secondary-container text-on-secondary-container',
  outlined: 'border border-outline text-on-surface-variant',
}
</script>
