<template>
  <component
    :is="link ?? 'button'"
    v-bind="$attrs"
    :to="to"
    :type="to ? undefined : type"
    :disabled="!to && (disabled || loading) ? true : undefined"
    :aria-disabled="to && disabled ? 'true' : undefined"
    :aria-busy="loading ? 'true' : undefined"
    :class="classes"
  >
    <M3Progress v-if="loading" circular :label="loadingLabel" class="!size-[18px]" />
    <M3Icon v-else-if="icon" :name="icon" :size="18" />
    <slot />
  </component>
</template>

<script setup lang="ts">
import { computed, resolveComponent } from 'vue'
import M3Icon from './Icon.vue'
import M3Progress from './Progress.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(defineProps<{
  variant?: 'filled' | 'tonal' | 'outlined' | 'text' | 'elevated'
  tone?: 'primary' | 'error'
  icon?: string
  to?: string | Record<string, unknown>
  type?: 'button' | 'submit' | 'reset'
  loading?: boolean
  loadingLabel?: string
  disabled?: boolean
}>(), { variant: 'filled', tone: 'primary', icon: undefined, to: undefined, type: 'button', loadingLabel: '' })

// Resolved only for links, so the primitive also mounts without the router (unit tests).
const link = computed(() => (props.to ? resolveComponent('NuxtLink') : null))

const VARIANTS: Record<string, Record<string, string>> = {
  filled: {
    primary: 'bg-primary text-on-primary',
    error: 'bg-error text-on-error',
  },
  tonal: {
    primary: 'bg-secondary-container text-on-secondary-container',
    error: 'bg-error-container text-on-error-container',
  },
  outlined: {
    primary: 'border border-outline text-primary',
    error: 'border border-outline text-error',
  },
  text: {
    primary: 'text-primary',
    error: 'text-error',
  },
  elevated: {
    primary: 'bg-surface-container-low text-primary shadow-elevation-1',
    error: 'bg-surface-container-low text-error shadow-elevation-1',
  },
}

const DISABLED = {
  filled: 'bg-on-surface/12 text-on-surface/38',
  tonal: 'bg-on-surface/12 text-on-surface/38',
  elevated: 'bg-on-surface/12 text-on-surface/38 shadow-none',
  outlined: 'border-on-surface/12 text-on-surface/38',
  text: 'text-on-surface/38',
}

const classes = computed(() => [
  'state-layer inline-flex h-10 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-full',
  'text-label-large no-underline select-none transition-shadow duration-200',
  props.variant === 'text' ? (props.icon ? 'pl-3 pr-4' : 'px-3') : (props.icon ? 'pl-4 pr-6' : 'px-6'),
  props.variant === 'filled' || props.variant === 'tonal' ? 'hover:shadow-elevation-1' : '',
  props.disabled || props.loading ? DISABLED[props.variant] : VARIANTS[props.variant]?.[props.tone],
])
</script>
