<template>
  <div class="min-w-0">
    <div class="relative">
      <!-- The control must carry the class "peer" and, for the floating label, placeholder=" ". -->
      <slot />
      <label
        :for="forId"
        :class="[
          'pointer-events-none absolute z-10 max-w-[calc(100%-2rem)] truncate transition-all duration-150',
          leading ? 'left-12' : 'left-4',
          floated
            ? 'top-0 left-4 -translate-y-1/2 text-body-small'
            : 'top-7 -translate-y-1/2 text-body-large peer-focus:top-0 peer-focus:left-4 peer-focus:text-body-small peer-not-placeholder-shown:top-0 peer-not-placeholder-shown:left-4 peer-not-placeholder-shown:text-body-small',
          error ? 'text-error' : disabled ? 'text-on-surface/38' : 'text-on-surface-variant peer-focus:text-primary peer-focus-within:text-primary',
        ]"
      >
        {{ label }}<span v-if="required" aria-hidden="true"> *</span>
      </label>
      <fieldset
        aria-hidden="true"
        :class="[
          floated ? '' : 'peer-focus:[&>legend]:max-w-full peer-not-placeholder-shown:[&>legend]:max-w-full',
          'pointer-events-none absolute inset-x-0 bottom-0 -top-[5px] m-0 min-w-0 rounded-xs border px-3 py-0',
          error ? 'border-error peer-focus:border-2' : disabled ? 'border-on-surface/12' : 'border-outline hover:border-on-surface peer-focus:border-2 peer-focus:border-primary peer-focus-within:border-2 peer-focus-within:border-primary peer-focus-within:[&>legend]:max-w-full',
          error ? 'peer-focus:border-error' : '',
        ]"
      >
        <legend
          :class="[
            'invisible h-[11px] max-w-[0.01px] whitespace-nowrap p-0 text-body-small transition-[max-width] duration-100',
            floated ? 'max-w-full' : '',
          ]"
        >
          <span class="px-1">{{ label }}<span v-if="required"> *</span></span>
        </legend>
      </fieldset>
    </div>
    <div v-if="error || hint || counter" class="flex gap-4 px-4 pt-1 text-body-small">
      <p :id="`${forId}-support`" :class="error ? 'text-error' : 'text-on-surface-variant'">{{ error || hint }}</p>
      <p v-if="counter" class="ml-auto text-on-surface-variant">{{ counter }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Outlined text field frame (Material 3): a floating label and a border that is interrupted behind the label
 * ("notch", drawn with a fieldset legend so it works on any background). Used by the text field and the select.
 */
withDefaults(defineProps<{
  forId: string
  label: string
  error?: string
  hint?: string
  counter?: string
  required?: boolean
  disabled?: boolean
  /** The label rests on the border permanently (select, date and file inputs). */
  floated?: boolean
  leading?: boolean
}>(), { error: '', hint: '', counter: '' })
</script>
