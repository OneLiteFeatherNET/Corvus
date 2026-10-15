<template>
  <v-row>
    <v-col cols="12" lg="7">
      <AppProblem :error="save.error.value" />
      <PoolFilterForm :form="form" :show-errors="showErrors" />
      <div class="d-flex flex-wrap ga-2 mt-4">
        <v-btn prepend-icon="mdi-content-save-outline" :loading="save.pending.value" @click="submit(false)">
          {{ $t('common.save') }}
        </v-btn>
        <v-btn variant="tonal" prepend-icon="mdi-eye-outline" :loading="save.pending.value" @click="submit(true)">
          {{ $t('pools.saveAndPreview') }}
        </v-btn>
        <v-btn variant="text" :to="current ? `/pools/${current.id}` : '/pools'">{{ $t('common.cancel') }}</v-btn>
      </div>
    </v-col>
    <v-col cols="12" lg="5">
      <PreviewPanel
        :preview="preview"
        :loading="previewAction.pending.value"
        :error="previewAction.error.value"
        :stale="dirty"
        :disabled="!current"
        @refresh="refreshPreview"
      />
      <p class="text-body-medium text-on-surface-variant mt-2">{{ $t('pools.preview.savedOnly') }}</p>
    </v-col>
  </v-row>
</template>

<script setup lang="ts">
import { emptyPoolForm, formToPayload, payloadToForm, validatePoolForm } from '~/lib/pool-filter'
import type { Pool, Preview } from '~/types'

const props = defineProps<{ pool?: Pool }>()

const pools = usePools()
const router = useRouter()

const current = ref<Pool | undefined>(props.pool)
const form = reactive(props.pool ? payloadToForm(props.pool) : emptyPoolForm())
const saved = ref(JSON.stringify(formToPayload(form)))
const dirty = computed(() => JSON.stringify(formToPayload(form)) !== saved.value)
const showErrors = ref(false)
const preview = ref<Preview | null>(null)

const save = useAction(async () => {
  const body = formToPayload(form)
  const result = current.value ? await pools.update(current.value.id, body) : await pools.create(body)
  current.value = result
  saved.value = JSON.stringify(body)
  return result
})
const previewAction = useAction(async (id: string) => {
  preview.value = await pools.preview(id)
})

async function submit(withPreview: boolean) {
  showErrors.value = true
  if (validatePoolForm(form).length) return
  const wasNew = !current.value
  const result = await save.run()
  if (!result) return
  if (withPreview) await previewAction.run(result.id)
  if (wasNew) await router.replace(`/pools/${result.id}/edit`)
  else if (!withPreview) await router.push(`/pools/${result.id}`)
}

function refreshPreview() {
  if (current.value) void previewAction.run(current.value.id)
}
</script>
