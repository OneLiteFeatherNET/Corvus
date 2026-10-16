<template>
  <M3Chip :tone="tone" :icon="icon" small>
    {{ $t(`${kind === 'job' ? 'jobs.status' : 'models.stage'}.${value}`) }}
  </M3Chip>
</template>

<script setup lang="ts">
import { chipTone, jobColor, jobIcon, stageColor, stageIcon } from '~/lib/format'

const props = defineProps<{ kind: 'job' | 'stage', value: string }>()
const tone = computed(() => chipTone(props.kind === 'job' ? jobColor(props.value) : stageColor(props.value)))
const icon = computed(() => (props.kind === 'job' ? jobIcon(props.value) : stageIcon(props.value)))
</script>
