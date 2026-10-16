import type { InjectionKey, Ref } from 'vue'

export interface TableLabels {
  loading: string
  empty: string
  select: string
  sortBy: string
  rowsPerPage: string
  previous: string
  next: string
  /** Template with {from}, {to} and {total}. */
  range: string
}

/** The app provides the translated labels once; a table can still take its own through the `labels` prop. */
export const TABLE_LABELS: InjectionKey<Ref<TableLabels>> = Symbol('m3-table-labels')
