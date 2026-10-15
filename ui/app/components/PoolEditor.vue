<template>
  <v-row>
    <v-col cols="12" lg="7">
      <AppProblem :error="save.error.value" />
      <PoolFilterForm :form="form" :show-errors="showErrors" />
      <div class="d-flex flex-wrap ga-2 mt-4">
        <v-btn prepend-icon="mdi-content-save-outline" :loading="save.pending.value" @click="submit">
          {{ $t('common.save') }}
        </v-btn>
        <v-btn variant="text" :to="current ? `/pools/${current.id}` : '/pools'">{{ $t('common.cancel') }}</v-btn>
      </div>
    </v-col>
    <v-col cols="12" lg="5">
      <PreviewPanel
        :preview="preview"
        :loading="previewPending"
        :error="previewError"
        :disabled="!filterValid"
        @refresh="refreshPreview"
      />
      <p class="text-body-medium text-on-surface-variant mt-2">
        {{ filterValid ? $t('pools.preview.live') : $t('pools.preview.invalid') }}
      </p>
    </v-col>
  </v-row>
</template>

<script setup lang="ts">
import { createLivePreview } from '~/lib/live-preview'
import { emptyPoolForm, formToPayload, payloadToForm, validatePoolForm } from '~/lib/pool-filter'
import type { Pool, Preview } from '~/types'

const props = defineProps<{ pool?: Pool }>()

const pools = usePools()
const router = useRouter()

const current = ref<Pool | undefined>(props.pool)
const form = reactive(props.pool ? payloadToForm(props.pool) : emptyPoolForm())
const showErrors = ref(false)
const preview = ref<Preview | null>(null)
const previewPending = ref(false)
const previewError = ref<Error | null>(null)

const save = useAction(async () => {
  const body = formToPayload(form)
  const result = current.value ? await pools.update(current.value.id, body) : await pools.create(body)
  current.value = result
  return result
})

// The preview only needs a valid filter; the name does not matter for it.
const filterValid = computed(() => validatePoolForm(form).every(e => e === 'nameRequired' || e === 'nameTooLong'))

const livePreview = createLivePreview({
  run: (filter, signal) => pools.previewFilter(filter, signal),
  onResult: (p) => { preview.value = p },
  onError: (e) => { previewError.value = e },
  onPending: (p) => { previewPending.value = p },
})

watch(
  () => JSON.stringify(formToPayload(form).filter),
  () => {
    if (filterValid.value) livePreview.schedule(formToPayload(form).filter)
    else livePreview.cancel()
  },
)
onMounted(() => {
  if (filterValid.value) void livePreview.now(formToPayload(form).filter)
})
onBeforeUnmount(livePreview.cancel)

async function submit() {
  showErrors.value = true
  if (validatePoolForm(form).length) return
  const wasNew = !current.value
  const result = await save.run()
  if (!result) return
  if (wasNew) await router.replace(`/pools/${result.id}/edit`)
  else await router.push(`/pools/${result.id}`)
}

function refreshPreview() {
  if (filterValid.value) void livePreview.now(formToPayload(form).filter)
}
</script>
