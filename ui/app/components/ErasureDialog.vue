<template>
  <M3Dialog
    :model-value="modelValue"
    :title="$t('players.erasure.title')"
    persistent
    alert
    describedby="erasure-desc"
    @update:model-value="$emit('update:modelValue', $event)"
    @after-leave="reset"
  >
    <div class="flex flex-col gap-4 pt-1">
      <M3Alert tone="warning">
        <span id="erasure-desc">{{ $t('players.erasure.warning') }}</span>
      </M3Alert>
      <AppProblem :error="action.error.value" />
      <p>
        {{ $t('players.erasure.target') }}:
        <span class="mono">{{ shortPseudonym(pseudonym) }}…</span>
      </p>
      <M3TextField
        v-model="typed"
        :label="$t('players.erasure.typeToConfirm', { word: ERASE_WORD })"
        autocomplete="off"
        autocapitalize="characters"
        spellcheck="false"
        :hint="$t('players.erasure.caseHint')"
      />
    </div>
    <template #actions>
      <M3Button variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</M3Button>
      <M3Button tone="error" :disabled="!confirmed" :loading="action.pending.value" :loading-label="$t('common.loading')" icon="mdi-delete-forever-outline" @click="submit">
        {{ $t('players.erasure.confirm') }}
      </M3Button>
    </template>
  </M3Dialog>
</template>

<script setup lang="ts">
import { buildErasureRequest, ERASE_WORD, isErasureConfirmed, shortPseudonym } from '~/lib/erasure'
import type { Erasure } from '~/types'

const props = defineProps<{ modelValue: boolean, pseudonym: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean], 'erased': [Erasure] }>()

const players = usePlayers()
const typed = ref('')
const confirmed = computed(() => isErasureConfirmed(typed.value))

// The guard lives in buildErasureRequest: without the exact word no request body exists, so no call is made.
const action = useAction(async () => {
  const body = buildErasureRequest(props.pseudonym, typed.value)
  if (!body) return
  const result = await players.erase(body)
  emit('erased', result)
  emit('update:modelValue', false)
})
const submit = () => action.run()

function reset() {
  typed.value = ''
  action.reset()
}
</script>
