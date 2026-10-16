<template>
  <button
    type="button"
    :role="selected === undefined ? 'menuitem' : 'menuitemradio'"
    :aria-checked="selected"
    :class="[
      'state-layer flex h-12 w-full items-center gap-3 px-3 text-start text-label-large',
      selected ? 'bg-secondary-container text-on-secondary-container' : 'text-on-surface',
    ]"
  >
    <M3Icon v-if="icon" :name="icon" :size="20" />
    <slot />
  </button>
</template>

<script setup lang="ts">
import M3Icon from './Icon.vue'

defineProps<{ icon?: string, selected?: boolean }>()
</script>
