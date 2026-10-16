// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3TextField from './TextField.vue'

const field = (props: Record<string, unknown> = {}, attrs: Record<string, unknown> = {}) =>
  mount(M3TextField, { props: { label: 'Name', modelValue: '', ...props }, attrs })

describe('M3TextField', () => {
  it('associates the label with the input', () => {
    const w = field()
    const input = w.get('input')
    expect(w.get('label').attributes('for')).toBe(input.attributes('id'))
    expect(w.get('label').text()).toBe('Name')
  })

  it('marks an error on the input and links the message', () => {
    const w = field({ error: 'Required' })
    const input = w.get('input')
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(w.get(`#${CSS.escape(input.attributes('aria-describedby')!)}`).text()).toBe('Required')
  })

  it('has no aria-invalid without an error', () => {
    expect(field().get('input').attributes('aria-invalid')).toBeUndefined()
  })

  it('emits the typed value', async () => {
    const w = field()
    await w.get('input').setValue('abc')
    expect(w.emitted('update:modelValue')?.[0]).toEqual(['abc'])
  })

  it('emits a number with the number modifier', async () => {
    const w = field({ modelModifiers: { number: true }, type: 'number' })
    await w.get('input').setValue('0.5')
    expect(w.emitted('update:modelValue')?.[0]).toEqual([0.5])
  })

  it('renders a textarea when multiline', () => {
    expect(field({ multiline: true }).find('textarea').exists()).toBe(true)
  })

  it('passes class to the field and other attributes to the control', () => {
    const w = field({}, { class: 'w-40', autocomplete: 'off' })
    expect(w.classes()).toContain('w-40')
    expect(w.get('input').attributes('autocomplete')).toBe('off')
    expect(w.get('input').classes()).not.toContain('w-40')
  })

  it('shows the counter', () => {
    expect(field({ modelValue: 'abcd', counter: 10 }).text()).toContain('4 / 10')
  })
})
