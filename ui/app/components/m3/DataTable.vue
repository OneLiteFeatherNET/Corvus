<template>
  <div class="overflow-hidden rounded-md border border-outline-variant bg-surface">
    <M3Progress v-if="loading" :label="labels.loading" class="!rounded-none" />
    <div class="overflow-x-auto">
      <table class="w-full border-collapse text-body-medium">
        <thead>
          <tr class="bg-surface-container text-on-surface-variant">
            <th v-if="selectable" scope="col" class="w-12 px-2">
              <span class="sr-only">{{ labels.select }}</span>
            </th>
            <th
              v-for="c in columns"
              :key="c.key"
              scope="col"
              :aria-sort="ariaSort(c)"
              :class="['h-12 whitespace-nowrap px-4 text-title-small', c.align === 'end' ? 'text-end' : 'text-start']"
            >
              <button
                v-if="c.sortable !== false && c.title"
                type="button"
                :class="['state-layer -mx-2 inline-flex items-center gap-1 rounded-sm px-2 py-1', c.align === 'end' ? 'flex-row-reverse' : '']"
                :aria-label="`${labels.sortBy} ${c.title}`"
                @click="toggleSort(c.key)"
              >
                {{ c.title }}
                <M3Icon v-if="sortKey === c.key" :name="sortDesc ? 'mdi-arrow-down' : 'mdi-arrow-up'" :size="16" />
              </button>
              <template v-else>{{ c.title }}</template>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in pageRows" :key="String(val(row, rowKey))" class="border-t border-outline-variant hover:bg-on-surface/8">
            <td v-if="selectable" class="w-12 px-2">
              <M3Checkbox
                :model-value="isSelected(row)"
                :aria-label="labels.select"
                @update:model-value="toggleRow(row, $event)"
              />
            </td>
            <td v-for="c in columns" :key="c.key" :class="['px-4 py-3 align-middle', c.align === 'end' ? 'text-end' : 'text-start']">
              <slot :name="`item.${c.key}`" :item="row" :value="val(row, c.key)">{{ val(row, c.key) ?? '' }}</slot>
            </td>
          </tr>
        </tbody>
      </table>
      <M3EmptyState v-if="!items.length && !loading" :title="labels.empty" />
    </div>
    <div
      v-if="items.length > PAGE_SIZES[0]!"
      class="flex flex-wrap items-center justify-end gap-x-6 gap-y-1 border-t border-outline-variant px-4 py-1 text-body-small text-on-surface-variant"
    >
      <label class="flex items-center gap-2">
        {{ labels.rowsPerPage }}
        <select
          v-model.number="pageSize"
          class="h-8 rounded-xs border border-outline-variant bg-transparent px-1 text-body-small text-on-surface"
        >
          <option v-for="n in PAGE_SIZES" :key="n" :value="n" class="bg-surface-container-high">{{ n }}</option>
        </select>
      </label>
      <span aria-live="polite">{{ labels.range.replace('{from}', String(from)).replace('{to}', String(to)).replace('{total}', String(sorted.length)) }}</span>
      <span class="flex">
        <M3IconButton icon="mdi-chevron-left" :label="labels.previous" :disabled="page === 0" @click="page--" />
        <M3IconButton icon="mdi-chevron-right" :label="labels.next" :disabled="to >= sorted.length" @click="page++" />
      </span>
    </div>
  </div>
</template>

<script setup lang="ts" generic="T extends object">
import { computed, inject, ref, watch } from 'vue'
import M3Checkbox from './Checkbox.vue'
import M3EmptyState from './EmptyState.vue'
import M3Icon from './Icon.vue'
import M3IconButton from './IconButton.vue'
import M3Progress from './Progress.vue'
import { TABLE_LABELS } from './table-labels'
import type { TableLabels } from './table-labels'

export interface Column {
  key: string
  title: string
  align?: 'start' | 'end'
  /** Defaults to true; the actions column sets false. */
  sortable?: boolean
}
const PAGE_SIZES = [10, 25, 50, 100]
const val = (row: object, key: string): unknown => (row as Record<string, unknown>)[key]

const props = withDefaults(defineProps<{
  columns: readonly Column[]
  items: readonly T[]
  rowKey?: string
  loading?: boolean
  labels?: TableLabels
  selectable?: boolean
  selected?: readonly T[]
}>(), { rowKey: 'id', labels: undefined, selected: () => [] })
const emit = defineEmits<{ 'update:selected': [T[]] }>()

const injected = inject(TABLE_LABELS, undefined)
const labels = computed<TableLabels>(() => {
  const l = props.labels ?? injected?.value
  if (!l) throw new Error('M3DataTable needs labels: pass the labels prop or provide TABLE_LABELS')
  return l
})

const sortKey = ref('')
const sortDesc = ref(false)
const page = ref(0)
const pageSize = ref(PAGE_SIZES[0]!)

function toggleSort(key: string) {
  if (sortKey.value !== key) {
    sortKey.value = key
    sortDesc.value = false
  }
  else if (!sortDesc.value) {
    sortDesc.value = true
  }
  else {
    sortKey.value = ''
    sortDesc.value = false
  }
}
function ariaSort(c: Column): 'ascending' | 'descending' | 'none' | undefined {
  if (c.sortable === false || !c.title) return undefined
  return sortKey.value === c.key ? (sortDesc.value ? 'descending' : 'ascending') : 'none'
}

function compare(a: unknown, b: unknown): number {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), undefined, { numeric: true })
}
const sorted = computed(() => {
  if (!sortKey.value) return [...props.items]
  const k = sortKey.value
  const dir = sortDesc.value ? -1 : 1
  return [...props.items].sort((x, y) => dir * compare(val(x, k), val(y, k)))
})

const from = computed(() => (sorted.value.length ? page.value * pageSize.value + 1 : 0))
const to = computed(() => Math.min(sorted.value.length, (page.value + 1) * pageSize.value))
const pageRows = computed(() => sorted.value.slice(page.value * pageSize.value, (page.value + 1) * pageSize.value))

// New data or a new page size can leave the current page past the end.
watch([() => props.items, pageSize], () => {
  const last = Math.max(0, Math.ceil(sorted.value.length / pageSize.value) - 1)
  if (page.value > last) page.value = last
})
watch(pageSize, () => (page.value = 0))

const isSelected = (row: T) => props.selected.some(s => val(s, props.rowKey) === val(row, props.rowKey))
function toggleRow(row: T, on: boolean) {
  const rest = props.selected.filter(s => val(s, props.rowKey) !== val(row, props.rowKey))
  emit('update:selected', on ? [...rest, row] : rest)
}
</script>
