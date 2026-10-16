<template>
  <M3Alert v-if="error" tone="error" role="alert" :title="title" class="mb-4">
    <p v-if="detail">{{ detail }}</p>
    <ul v-if="apiError && apiError.reasons.length" class="mt-2 list-disc pl-5" data-test="reasons">
      <li v-for="(r, i) in apiError.reasons" :key="i">{{ r }}</li>
    </ul>
    <ul v-if="apiError && apiError.errors.length" class="mt-2 list-disc pl-5" data-test="validation-errors">
      <li v-for="(e, i) in apiError.errors" :key="i">
        <span v-if="e.line !== undefined">{{ $t('common.line', { n: e.line }) }}: </span>
        <span v-if="e.loc" class="mono">{{ e.loc.join('.') }}: </span>{{ e.msg }}
      </li>
    </ul>
  </M3Alert>
</template>

<script setup lang="ts">
import { ApiError, NetworkError } from '~/lib/problem'

const props = defineProps<{ error?: Error | null }>()
const { t } = useI18n()

const apiError = computed(() => (props.error instanceof ApiError ? props.error : null))
const title = computed(() => {
  if (props.error instanceof NetworkError) return t('errors.network')
  if (apiError.value) return `${apiError.value.status} ${apiError.value.problem.title}`
  return t('errors.unexpected')
})
// Reasons from the API are shown verbatim; the detail is only repeated when it adds something.
const detail = computed(() => {
  if (props.error instanceof NetworkError) return t('errors.networkHint')
  if (apiError.value) return apiError.value.problem.detail ?? ''
  return props.error?.message ?? ''
})
</script>
