<template>
  <label :class="['inline-flex min-h-10 items-center gap-3 text-body-large', disabled ? 'text-on-surface/38' : 'text-on-surface']">
    <button
      type="button"
      role="switch"
      :aria-checked="modelValue"
      :disabled="disabled"
      :class="[
        'state-layer relative inline-flex h-8 w-13 shrink-0 items-center rounded-full border-2 transition-colors',
        modelValue ? 'border-primary bg-primary text-on-primary' : 'border-outline bg-surface-container-highest text-outline',
      ]"
      @click="$emit('update:modelValue', !modelValue)"
    >
      <span
        :class="[
          'absolute rounded-full transition-all duration-200',
          modelValue ? 'left-[1.625rem] size-6 bg-on-primary' : 'left-1 size-4 bg-outline',
        ]"
      />
    </button>
    <slot />
  </label>
</template>

<script setup lang="ts">
defineProps<{ modelValue: boolean, disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [boolean] }>()
</script>
