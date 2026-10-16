<template>
  <div class="flex flex-col items-center gap-2 px-4 py-10 text-center text-on-surface-variant">
    <M3Icon v-if="icon" :name="icon" :size="40" />
    <p class="text-title-medium text-on-surface">{{ title }}</p>
    <p v-if="$slots.default" class="text-body-medium"><slot /></p>
  </div>
</template>

<script setup lang="ts">
import M3Icon from './Icon.vue'

withDefaults(defineProps<{ title: string, icon?: string }>(), { icon: 'mdi-tray-full' })
</script>
