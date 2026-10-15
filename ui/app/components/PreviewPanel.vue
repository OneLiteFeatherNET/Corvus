<template>
  <v-card variant="flat" color="surface-container-low" aria-live="polite" :aria-busy="loading">
    <v-card-title class="text-title-large d-flex align-center">
      {{ $t('pools.preview.title') }}
      <v-spacer />
      <v-btn
        size="small"
        variant="tonal"
        prepend-icon="mdi-refresh"
        :loading="loading"
        :disabled="disabled"
        @click="$emit('refresh')"
      >
        {{ $t('pools.preview.refresh') }}
      </v-btn>
    </v-card-title>
    <v-card-text>
      <AppProblem :error="error" />
      <v-alert v-if="stale" type="info" variant="tonal" density="compact" class="mb-3">
        {{ $t('pools.preview.stale') }}
      </v-alert>
      <p v-if="!preview && !error && !loading" class="text-on-surface-variant">{{ $t('pools.preview.none') }}</p>
      <v-progress-linear v-if="loading" indeterminate class="mb-3" :aria-label="$t('common.loading')" />
      <dl v-if="preview" class="preview-grid">
        <div v-for="row in rows" :key="row.key">
          <dt class="text-label-large text-on-surface-variant">{{ $t(`pools.preview.${row.key}`) }}</dt>
          <dd class="text-headline-small">{{ formatNumber(row.value, locale) }}</dd>
        </div>
      </dl>
      <div v-if="preview" class="mt-4">
        <div class="text-label-large mb-1">{{ $t('pools.preview.labelDistribution') }}</div>
        <ul class="d-flex flex-wrap ga-2 pa-0" style="list-style: none">
          <li v-for="k in ['legit', 'cheat', 'unlabeled']" :key="k">
            <v-chip label variant="tonal" size="small">{{ $t(`labels.value.${k}`) }}: {{ formatNumber(preview.label_distribution[k] ?? 0, locale) }}</v-chip>
          </li>
        </ul>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { formatNumber } from '~/lib/format'
import type { Preview } from '~/types'

const props = defineProps<{
  preview: Preview | null
  loading?: boolean
  error?: Error | null
  stale?: boolean
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

<style scoped>
.preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
}
</style>
