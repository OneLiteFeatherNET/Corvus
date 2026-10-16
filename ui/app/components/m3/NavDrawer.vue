<template>
  <nav v-if="!modal" :aria-label="label" class="w-72 shrink-0 px-3 py-3">
    <ul class="flex flex-col gap-1">
      <li v-for="item in items" :key="item.to">
        <M3NavItem :item="item" :active="isActive(item.to)" />
      </li>
    </ul>
  </nav>
  <dialog
    v-else
    ref="el"
    :aria-label="label"
    class="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-[min(20rem,85vw)] flex-col rounded-r-xl bg-surface-container-low p-3 text-on-surface shadow-elevation-3 open:flex backdrop:bg-scrim/32"
    @cancel.prevent="$emit('update:open', false)"
    @click="onBackdrop"
    @close="$emit('update:open', false)"
  >
    <div class="mb-2 flex items-center justify-between px-2">
      <slot name="header" />
      <M3IconButton icon="mdi-close" :label="closeLabel" @click="$emit('update:open', false)" />
    </div>
    <nav :aria-label="label">
      <ul class="flex flex-col gap-1">
        <li v-for="item in items" :key="item.to">
          <M3NavItem :item="item" :active="isActive(item.to)" @click="$emit('update:open', false)" />
        </li>
      </ul>
    </nav>
  </dialog>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import M3IconButton from './IconButton.vue'
import M3NavItem from './NavItem.vue'
import type { NavEntry } from './types'

const props = withDefaults(defineProps<{
  items: readonly NavEntry[]
  /** Current route path; the matching entry is marked with aria-current="page". */
  activePath: string
  label: string
  closeLabel?: string
  /** Modal sheet (small screens) instead of the permanent standard drawer. */
  modal?: boolean
  open?: boolean
}>(), { closeLabel: '', open: false })
const emit = defineEmits<{ 'update:open': [boolean] }>()

const el = ref<HTMLDialogElement>()

function isActive(to: string): boolean {
  return to === '/' ? props.activePath === '/' : props.activePath === to || props.activePath.startsWith(`${to}/`)
}

function sync(open: boolean) {
  const d = el.value
  if (!d) return
  if (open && !d.open) d.showModal()
  else if (!open && d.open) d.close()
}
watch(() => [props.open, props.modal], async () => {
  await nextTick()
  sync(props.open && !!props.modal)
})
onMounted(() => sync(props.open && !!props.modal))

function onBackdrop(e: MouseEvent) {
  if (e.target === el.value) emit('update:open', false)
}
</script>
