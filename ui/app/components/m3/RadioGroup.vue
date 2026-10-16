<template>
  <fieldset class="m-0 min-w-0 border-0 p-0">
    <legend class="mb-1 text-label-large text-on-surface-variant">{{ legend }}</legend>
    <label v-for="o in options" :key="o.value" class="flex min-h-10 items-center gap-2 text-body-large">
      <span class="state-layer inline-flex size-10 items-center justify-center rounded-full text-primary">
        <input
          type="radio"
          :name="name"
          :value="o.value"
          :checked="modelValue === o.value"
          class="size-[18px] cursor-pointer accent-primary"
          @change="$emit('update:modelValue', o.value)"
        >
      </span>
      {{ o.label }}
    </label>
  </fieldset>
</template>

<script setup lang="ts">
import { useId } from 'vue'

defineProps<{ modelValue: string, legend: string, options: readonly { value: string, label: string }[] }>()
defineEmits<{ 'update:modelValue': [string] }>()
const name = useId()
</script>
