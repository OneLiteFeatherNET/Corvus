<template>
  <div ref="root" class="relative inline-block" @keydown.esc.stop="close(true)" @focusout="onFocusOut">
    <slot name="activator" :props="activatorProps" :open="open" :toggle="toggle" />
    <div
      v-if="open"
      :id="panelId"
      :role="menu ? 'menu' : 'group'"
      :aria-label="label"
      :class="[
        'absolute top-full z-50 mt-1 min-w-48 max-w-[calc(100vw-2rem)] rounded-xs bg-surface-container text-on-surface shadow-elevation-2',
        align === 'end' ? 'right-0' : 'left-0',
      ]"
      @keydown="onKey"
    >
      <slot :close="() => close(true)" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'

/**
 * Popup anchored to its activator. With `menu` the panel is a menu (arrow keys move between the
 * role="menuitem" children); otherwise it is a plain popover for arbitrary content. Closes on Esc, outside
 * click and when focus leaves.
 */
const props = withDefaults(defineProps<{ label: string, align?: 'start' | 'end', menu?: boolean }>(), { align: 'end', menu: true })

const open = ref(false)
const root = ref<HTMLElement>()
const panelId = useId()
const activatorProps = computed(() => ({
  'aria-haspopup': props.menu ? 'menu' : 'dialog',
  'aria-expanded': String(open.value),
  'aria-controls': open.value ? panelId : undefined,
}))

async function toggle() {
  open.value = !open.value
  if (open.value) {
    await nextTick()
    focusables()[0]?.focus()
  }
}
function close(restoreFocus = false) {
  if (!open.value) return
  open.value = false
  if (restoreFocus) root.value?.querySelector<HTMLElement>('[aria-haspopup]')?.focus()
}
function focusables(): HTMLElement[] {
  const panel = root.value?.querySelector<HTMLElement>(`[id="${panelId}"]`)
  return panel ? [...panel.querySelectorAll<HTMLElement>('[role^="menuitem"],a[href],button:not(:disabled),input,select')] : []
}
function onKey(e: KeyboardEvent) {
  if (!props.menu || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return
  e.preventDefault()
  const items = focusables()
  const i = items.indexOf(document.activeElement as HTMLElement)
  items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus()
}
function onFocusOut(e: FocusEvent) {
  const next = e.relatedTarget as Node | null
  if (next && !root.value?.contains(next)) close()
}
function onOutside(e: PointerEvent) {
  if (open.value && !root.value?.contains(e.target as Node)) close()
}
onMounted(() => document.addEventListener('pointerdown', onOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutside))
</script>
