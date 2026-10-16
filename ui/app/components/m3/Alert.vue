<template>
  <div :role="role" :class="['flex gap-3 rounded-md p-4', TONES[tone]]">
    <M3Icon :name="ICON[tone]" class="mt-0.5" />
    <div class="min-w-0 flex-1 text-body-medium">
      <p v-if="title" class="text-title-small">{{ title }}</p>
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import M3Icon from './Icon.vue'

withDefaults(defineProps<{
  tone?: 'info' | 'success' | 'warning' | 'error'
  title?: string
  role?: 'alert' | 'status'
}>(), { tone: 'info', title: '', role: 'status' })

const TONES = {
  info: 'bg-secondary-container text-on-secondary-container',
  success: 'bg-success-container text-on-success-container',
  warning: 'bg-warning-container text-on-warning-container',
  error: 'bg-error-container text-on-error-container',
}
const ICON = {
  info: 'mdi-information-outline',
  success: 'mdi-check-circle-outline',
  warning: 'mdi-alert-outline',
  error: 'mdi-alert-circle-outline',
}
</script>
