<template>
  <component
    :is="link"
    :to="item.to"
    :aria-current="active ? 'page' : undefined"
    :class="[
      'state-layer flex h-14 items-center gap-3 rounded-full px-4 text-label-large no-underline',
      active ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface-variant',
    ]"
    @click="$emit('click')"
  >
    <M3Icon :name="item.icon" />
    {{ item.label }}
  </component>
</template>

<script setup lang="ts">
import { resolveComponent } from 'vue'
import M3Icon from './Icon.vue'
import type { NavEntry } from './types'

defineProps<{ item: NavEntry, active: boolean }>()
defineEmits<{ click: [] }>()
const link = resolveComponent('NuxtLink')
</script>
