<template>
  <v-chip :color="color" :prepend-icon="icon" size="small" label variant="tonal">
    {{ $t(`${kind === 'job' ? 'jobs.status' : 'models.stage'}.${value}`) }}
  </v-chip>
</template>

<script setup lang="ts">
import { jobColor, jobIcon, stageColor, stageIcon } from '~/lib/format'

const props = defineProps<{ kind: 'job' | 'stage', value: string }>()
const color = computed(() => (props.kind === 'job' ? jobColor(props.value) : stageColor(props.value)))
const icon = computed(() => (props.kind === 'job' ? jobIcon(props.value) : stageIcon(props.value)))
</script>
