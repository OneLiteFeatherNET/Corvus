// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3DataTable from './DataTable.vue'
import type { TableLabels } from './table-labels'

const labels: TableLabels = {
  loading: 'Loading', empty: 'Nothing here', select: 'Select row', sortBy: 'Sort by', rowsPerPage: 'Rows per page',
  previous: 'Previous page', next: 'Next page', range: '{from}-{to} of {total}',
}
const columns = [{ key: 'name', title: 'Name' }, { key: 'size', title: 'Size', align: 'end' as const }, { key: 'actions', title: '', sortable: false }]
const items = Array.from({ length: 23 }, (_, i) => ({ id: `r${i}`, name: `n${String(22 - i).padStart(2, '0')}`, size: i }))

const table = (props: Record<string, unknown> = {}) => mount(M3DataTable, { props: { columns, items, labels, ...props } })
const names = (w: ReturnType<typeof table>) => w.findAll('tbody tr').map(r => r.findAll('td')[0]!.text())

describe('M3DataTable', () => {
  it('shows the first page and the range', () => {
    const w = table()
    expect(w.findAll('tbody tr')).toHaveLength(10)
    expect(w.text()).toContain('1-10 of 23')
  })

  it('moves to the next and the previous page', async () => {
    const w = table()
    await w.get('button[aria-label="Next page"]').trigger('click')
    expect(w.text()).toContain('11-20 of 23')
    await w.get('button[aria-label="Previous page"]').trigger('click')
    expect(w.text()).toContain('1-10 of 23')
  })

  it('disables previous on the first page and next on the last', async () => {
    const w = table()
    expect(w.get('button[aria-label="Previous page"]').attributes('disabled')).toBeDefined()
    await w.get('button[aria-label="Next page"]').trigger('click')
    await w.get('button[aria-label="Next page"]').trigger('click')
    expect(w.text()).toContain('21-23 of 23')
    expect(w.get('button[aria-label="Next page"]').attributes('disabled')).toBeDefined()
  })

  it('changes the page size', async () => {
    const w = table()
    await w.get('select').setValue('25')
    expect(w.findAll('tbody tr')).toHaveLength(23)
  })

  it('sorts ascending, then descending, then back to the given order', async () => {
    const w = table({ items: items.slice(0, 3) })
    const sort = w.get('button[aria-label="Sort by Name"]')
    expect(w.findAll('th')[0]!.attributes('aria-sort')).toBe('none')
    await sort.trigger('click')
    expect(names(w)).toEqual(['n20', 'n21', 'n22'])
    expect(w.findAll('th')[0]!.attributes('aria-sort')).toBe('ascending')
    await sort.trigger('click')
    expect(names(w)).toEqual(['n22', 'n21', 'n20'])
    expect(w.findAll('th')[0]!.attributes('aria-sort')).toBe('descending')
    await sort.trigger('click')
    expect(names(w)).toEqual(['n22', 'n21', 'n20'])
    expect(w.findAll('th')[0]!.attributes('aria-sort')).toBe('none')
  })

  it('does not offer sorting for the actions column', () => {
    expect(table().findAll('th')[2]!.attributes('aria-sort')).toBeUndefined()
  })

  it('renders cell slots', () => {
    const w = mount(M3DataTable, { props: { columns, items: items.slice(0, 1), labels }, slots: { 'item.name': '<template #item.name="{ item }">[{{ item.name }}]</template>' } })
    expect(w.get('td').text()).toBe('[n22]')
  })

  it('shows the empty text and no pagination without rows', () => {
    const w = table({ items: [] })
    expect(w.text()).toContain('Nothing here')
    expect(w.find('select').exists()).toBe(false)
  })

  it('selects rows with a checkbox', async () => {
    const w = table({ selectable: true, items: items.slice(0, 3) })
    await w.findAll('tbody input[type=checkbox]')[1]!.setValue(true)
    expect(w.emitted('update:selected')?.[0]).toEqual([[items[1]]])
  })
})
