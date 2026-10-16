<template>
  <M3FieldFrame :for-id="id" :label="label" :hint="hint" floated>
    <div
      class="peer flex min-h-14 flex-wrap items-center gap-1.5 rounded-xs px-3 pt-4 pb-2"
      @click="focusInput"
    >
      <M3Chip
        v-for="(v, i) in modelValue"
        :key="`${v}-${i}`"
        small
        tone="secondary"
        removable
        :remove-label="`${removeLabel} ${v}`"
        @remove="removeAt(i)"
      >
        {{ v }}
      </M3Chip>
      <input
        :id="id"
        ref="input"
        v-model="draft"
        type="text"
        autocomplete="off"
        :aria-describedby="hint ? `${id}-support` : undefined"
        class="min-w-24 flex-1 bg-transparent py-1 text-body-large text-on-surface outline-none"
        @keydown="onKey"
        @blur="commit"
      >
    </div>
  </M3FieldFrame>
</template>

<script setup lang="ts">
import { ref, useId } from 'vue'
import M3Chip from './Chip.vue'
import M3FieldFrame from './FieldFrame.vue'

const props = withDefaults(defineProps<{
  modelValue: readonly string[]
  label: string
  hint?: string
  removeLabel?: string
}>(), { hint: '', removeLabel: '' })
const emit = defineEmits<{ 'update:modelValue': [string[]] }>()

const id = useId()
const input = ref<HTMLInputElement>()
const draft = ref('')

function commit() {
  const v = draft.value.trim()
  draft.value = ''
  if (v && !props.modelValue.includes(v)) emit('update:modelValue', [...props.modelValue, v])
}
function removeAt(i: number) {
  emit('update:modelValue', props.modelValue.filter((_, n) => n !== i))
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    commit()
  }
  else if (e.key === 'Backspace' && !draft.value && props.modelValue.length) {
    removeAt(props.modelValue.length - 1)
  }
}
function focusInput() {
  input.value?.focus()
}
</script>
