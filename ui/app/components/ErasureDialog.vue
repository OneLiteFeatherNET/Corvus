<template>
  <v-dialog :model-value="modelValue" max-width="560" persistent @update:model-value="$emit('update:modelValue', $event)" @after-leave="reset">
    <v-card :title="$t('players.erasure.title')" role="alertdialog" aria-describedby="erasure-desc">
      <v-card-text>
        <v-alert type="warning" variant="tonal" class="mb-4" icon="mdi-alert-outline">
          <span id="erasure-desc">{{ $t('players.erasure.warning') }}</span>
        </v-alert>
        <AppProblem :error="action.error.value" />
        <p class="mb-2">
          {{ $t('players.erasure.target') }}:
          <span class="mono">{{ shortPseudonym(pseudonym) }}…</span>
        </p>
        <v-text-field
          ref="confirmField"
          v-model="typed"
          :label="$t('players.erasure.typeToConfirm', { word: ERASE_WORD })"
          autocomplete="off"
          autocapitalize="characters"
          spellcheck="false"
          :hint="$t('players.erasure.caseHint')"
          persistent-hint
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:modelValue', false)">{{ $t('common.cancel') }}</v-btn>
        <v-btn color="error" variant="flat" :disabled="!confirmed" :loading="action.pending.value" prepend-icon="mdi-delete-forever-outline" @click="submit">
          {{ $t('players.erasure.confirm') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { buildErasureRequest, ERASE_WORD, isErasureConfirmed, shortPseudonym } from '~/lib/erasure'
import { rememberErasureId } from '~/lib/erasure-log'
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
  rememberErasureId(localStorage, result.id)
  emit('erased', result)
  emit('update:modelValue', false)
})
const submit = () => action.run()

function reset() {
  typed.value = ''
  action.reset()
}
</script>
