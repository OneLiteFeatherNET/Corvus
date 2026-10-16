<template>
  <label :class="['inline-flex min-h-10 items-center gap-2 text-body-large', disabled ? 'text-on-surface/38' : 'text-on-surface']">
    <span class="state-layer inline-flex size-10 items-center justify-center rounded-full text-primary">
      <input
        type="checkbox"
        :checked="modelValue"
        :disabled="disabled"
        :aria-label="ariaLabel || undefined"
        class="size-[18px] cursor-pointer accent-primary"
        @change="onChange"
      >
    </span>
    <slot />
  </label>
</template>

<script setup lang="ts">
defineProps<{ modelValue?: boolean, disabled?: boolean, ariaLabel?: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

function onChange(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).checked)
}
</script>
