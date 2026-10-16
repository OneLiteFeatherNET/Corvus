<template>
  <dialog
    ref="el"
    :aria-labelledby="titleId"
    :aria-describedby="describedby || undefined"
    aria-modal="true"
    :role="alert ? 'alertdialog' : 'dialog'"
    :class="[
      'm-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] flex-col overflow-hidden rounded-xl bg-surface-container-high p-0',
      'text-on-surface shadow-elevation-3 open:flex backdrop:bg-scrim/32',
      WIDTHS[width],
    ]"
    @cancel.prevent="onCancel"
    @click="onBackdrop"
    @close="onClose"
  >
    <div v-if="rendered" class="flex min-h-0 flex-1 flex-col">
      <h2 :id="titleId" class="px-6 pt-6 pb-4 text-headline-small">{{ title }}</h2>
      <div class="min-h-0 flex-1 overflow-y-auto px-6 pb-2">
        <slot />
      </div>
      <div v-if="$slots.actions" class="flex flex-wrap justify-end gap-2 px-6 pt-4 pb-6">
        <slot name="actions" />
      </div>
    </div>
  </dialog>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, useId, watch } from 'vue'

/**
 * Modal dialog on the native <dialog> element: the browser traps focus, marks the rest of the page inert,
 * closes on Esc and returns focus to the opener. `persistent` ignores Esc and a click on the scrim.
 */
const props = withDefaults(defineProps<{
  modelValue: boolean
  title: string
  width?: 'sm' | 'md' | 'lg'
  persistent?: boolean
  /** Destructive confirmation: announced as an alert dialog. */
  alert?: boolean
  describedby?: string
}>(), { width: 'md', describedby: '' })
const emit = defineEmits<{ 'update:modelValue': [boolean], 'afterLeave': [] }>()

const WIDTHS = { sm: 'max-w-[28rem]', md: 'max-w-[35rem]', lg: 'max-w-[56rem]' }
const el = ref<HTMLDialogElement>()
const titleId = useId()
// The content is created on first open and kept afterwards, so a closed dialog costs nothing.
const rendered = ref(props.modelValue)

async function sync(open: boolean) {
  const d = el.value
  if (!d) return
  if (open && !d.open) {
    rendered.value = true
    await nextTick()
    d.showModal()
  }
  else if (!open && d.open) {
    d.close()
  }
}

watch(() => props.modelValue, v => void sync(v))
onMounted(() => void sync(props.modelValue))

function onCancel() {
  if (!props.persistent) emit('update:modelValue', false)
}
function onBackdrop(e: MouseEvent) {
  if (e.target === el.value && !props.persistent) emit('update:modelValue', false)
}
function onClose() {
  if (props.modelValue) emit('update:modelValue', false)
  emit('afterLeave')
}
</script>
