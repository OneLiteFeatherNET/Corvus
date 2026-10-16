// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3Dialog from './Dialog.vue'

const open = (props: Record<string, unknown> = {}) =>
  mount(M3Dialog, {
    props: { modelValue: true, title: 'Add entry', ...props },
    slots: { default: '<input id="first">', actions: '<button>Save</button>' },
    attachTo: document.body,
  })

describe('M3Dialog', () => {
  it('opens as a modal dialog named by its title', async () => {
    const w = open()
    await w.vm.$nextTick()
    const d = w.get('dialog').element as HTMLDialogElement
    expect(d.open).toBe(true)
    expect(w.get('dialog').attributes('aria-modal')).toBe('true')
    expect(document.getElementById(w.get('dialog').attributes('aria-labelledby')!)?.textContent).toBe('Add entry')
    w.unmount()
  })

  it('asks to close on Esc', async () => {
    const w = open()
    await w.vm.$nextTick()
    await w.get('dialog').trigger('cancel')
    expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    w.unmount()
  })

  it('ignores Esc when persistent', async () => {
    const w = open({ persistent: true })
    await w.vm.$nextTick()
    await w.get('dialog').trigger('cancel')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    w.unmount()
  })

  it('closes on a click on the scrim but not on the content', async () => {
    const w = open()
    await w.vm.$nextTick()
    await w.get('#first').trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    await w.get('dialog').trigger('click')
    expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    w.unmount()
  })

  it('closes the native dialog when the model turns false and reports afterLeave', async () => {
    const w = open()
    await w.vm.$nextTick()
    await w.setProps({ modelValue: false })
    expect((w.get('dialog').element as HTMLDialogElement).open).toBe(false)
    expect(w.emitted('afterLeave')).toBeTruthy()
    w.unmount()
  })

  it('does not render its content before the first open', () => {
    const w = open({ modelValue: false })
    expect(w.find('#first').exists()).toBe(false)
    w.unmount()
  })

  it('announces a destructive confirmation as an alert dialog', () => {
    const w = open({ alert: true })
    expect(w.get('dialog').attributes('role')).toBe('alertdialog')
    w.unmount()
  })
})
