<template>
  <svg
    viewBox="0 0 24 24"
    fill="currentColor"
    class="inline-block shrink-0"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    focusable="false"
  >
    <path :d="iconPath(name)" />
  </svg>
</template>

<script setup lang="ts">
import { iconPath } from '../../lib/icons'

withDefaults(defineProps<{ name: string, size?: number, label?: string }>(), { size: 24, label: '' })
</script>
