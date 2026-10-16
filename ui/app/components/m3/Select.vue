<template>
  <M3FieldFrame :class="$attrs.class as string" :style="$attrs.style as string" :for-id="id" :label="label" :error="error" :hint="hint" :required="required" :disabled="disabled" floated>
    <select
      :id="id"
      v-bind="inputAttrs()"
      :value="modelValue"
      :required="required"
      :disabled="disabled"
      :aria-invalid="error ? 'true' : undefined"
      :aria-describedby="error || hint ? `${id}-support` : undefined"
      class="peer block h-14 w-full appearance-none rounded-xs bg-transparent pr-12 pl-4 text-body-large text-on-surface outline-none"
      @change="onChange"
    >
      <option v-for="o in options" :key="String(o.value)" :value="o.value" class="bg-surface-container-high text-on-surface">
        {{ o.title }}
      </option>
    </select>
    <M3Icon name="mdi-chevron-down" class="pointer-events-none absolute top-4 right-3 text-on-surface-variant" />
  </M3FieldFrame>
</template>

<script setup lang="ts" generic="V extends string | number">
import { computed, useAttrs, useId } from 'vue'
import M3FieldFrame from './FieldFrame.vue'
import M3Icon from './Icon.vue'

defineOptions({ inheritAttrs: false })

// class and style belong to the whole field; every other attribute (autocomplete, min, step, ...) to the control.
const attrs = useAttrs()
// A function, not a computed: the attrs object is not reactive.
function inputAttrs() {
  const { class: _c, style: _s, ...rest } = attrs
  return rest
}

type Item = V | { value: V, title: string }
const props = withDefaults(defineProps<{
  modelValue: V
  items: readonly Item[]
  label: string
  error?: string
  hint?: string
  required?: boolean
  disabled?: boolean
}>(), { error: '', hint: '' })
const emit = defineEmits<{ 'update:modelValue': [V] }>()

const id = useId()
const options = computed(() => props.items.map((i): { value: V, title: string } => (typeof i === 'object' ? i : { value: i, title: String(i) })))

// The native select reports strings; hand back the original typed value (e.g. numeric limits).
function onChange(e: Event) {
  const raw = (e.target as HTMLSelectElement).value
  emit('update:modelValue', options.value.find(o => String(o.value) === raw)?.value ?? (raw as V))
}
</script>
