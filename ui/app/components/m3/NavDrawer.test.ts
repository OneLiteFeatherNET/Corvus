// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3NavDrawer from './NavDrawer.vue'
import { visibleNav } from '../../lib/nav'
import { can } from '../../lib/roles'
import type { Role } from '../../types'

const NuxtLink = { props: ['to'], template: '<a :href="to"><slot /></a>' }
const entries = (role: Role) => visibleNav(a => can([role], a)).map(i => ({ to: i.to, icon: i.icon, label: i.key }))
const drawer = (role: Role, activePath = '/') =>
  mount(M3NavDrawer, { props: { items: entries(role), activePath, label: 'Main' }, global: { components: { NuxtLink } } })

describe('M3NavDrawer role gating', () => {
  it('shows only the dashboard to a viewer without further rights', () => {
    const labels = drawer('viewer').findAll('a').map(a => a.text())
    expect(labels).toContain('dashboard')
    expect(labels).not.toContain('blocklist')
    expect(labels).not.toContain('players')
  })

  it('shows blocklist and players to an admin', () => {
    const labels = drawer('admin').findAll('a').map(a => a.text())
    expect(labels).toEqual(expect.arrayContaining(['blocklist', 'players', 'models']))
  })

  it('marks the current page, including nested routes', () => {
    const w = drawer('admin', '/models/gbm/1.0')
    const current = w.findAll('a[aria-current="page"]')
    expect(current.map(a => a.text())).toEqual(['models'])
  })

  it('marks the dashboard only for the root path', () => {
    expect(drawer('admin', '/pools').findAll('a[aria-current="page"]').map(a => a.text())).toEqual(['pools'])
  })

  it('names the navigation landmark', () => {
    expect(drawer('admin').get('nav').attributes('aria-label')).toBe('Main')
  })
})
