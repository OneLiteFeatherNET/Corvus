<template>
  <M3Card aria-live="polite" :aria-busy="loading">
    <div class="mb-3 flex items-center justify-between gap-3">
      <h2 class="text-title-large">{{ $t('pools.preview.title') }}</h2>
      <M3Button variant="tonal" icon="mdi-refresh" :loading="loading" :loading-label="$t('common.loading')" :disabled="disabled" @click="$emit('refresh')">
        {{ $t('pools.preview.refresh') }}
      </M3Button>
    </div>
    <AppProblem :error="error" />
    <p v-if="!preview && !error && !loading" class="text-on-surface-variant">{{ $t('pools.preview.none') }}</p>
    <M3Progress v-if="loading" class="mb-3" :label="$t('common.loading')" />
    <dl v-if="preview" class="grid grid-cols-[repeat(auto-fit,minmax(8.5rem,1fr))] gap-4">
      <div v-for="row in rows" :key="row.key">
        <dt class="text-label-large text-on-surface-variant">{{ $t(`pools.preview.${row.key}`) }}</dt>
        <dd class="text-headline-small">{{ formatNumber(row.value, locale) }}</dd>
      </div>
    </dl>
    <div v-if="preview" class="mt-4">
      <div class="mb-1 text-label-large">{{ $t('pools.preview.labelDistribution') }}</div>
      <ul class="flex list-none flex-wrap gap-2">
        <li v-for="k in ['legit', 'cheat', 'unlabeled']" :key="k">
          <M3Chip small>{{ $t(`labels.value.${k}`) }}: {{ formatNumber(preview.label_distribution[k] ?? 0, locale) }}</M3Chip>
        </li>
      </ul>
    </div>
  </M3Card>
</template>

<script setup lang="ts">
import { formatNumber } from '~/lib/format'
import type { Preview } from '~/types'

const props = defineProps<{
  preview: Preview | null
  loading?: boolean
  error?: Error | null
  disabled?: boolean
}>()
defineEmits<{ refresh: [] }>()

const { locale } = useI18n()
const rows = computed(() => [
  { key: 'sessions', value: props.preview?.sessions ?? 0 },
  { key: 'segments', value: props.preview?.segments ?? 0 },
  { key: 'distinctPlayers', value: props.preview?.distinct_players ?? 0 },
  { key: 'eventsFiles', value: props.preview?.events_files ?? 0 },
  { key: 'framesEstimate', value: props.preview?.total_frames_estimate ?? 0 },
])
</script>
