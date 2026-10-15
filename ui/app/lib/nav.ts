import type { Action } from './roles'

export interface NavItem {
  key: string
  to: string
  icon: string
  /** Action the role needs to see the entry; the dashboard is open to every signed-in user. */
  requires?: Action
}

export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'dashboard', to: '/', icon: 'mdi-view-dashboard-outline' },
  { key: 'pools', to: '/pools', icon: 'mdi-database-search-outline', requires: 'pools.read' },
  { key: 'snapshots', to: '/snapshots', icon: 'mdi-camera-outline', requires: 'snapshots.read' },
  { key: 'jobs', to: '/jobs', icon: 'mdi-cog-play-outline', requires: 'jobs.read' },
  { key: 'models', to: '/models', icon: 'mdi-brain', requires: 'models.read' },
  { key: 'labels', to: '/labels', icon: 'mdi-tag-multiple-outline', requires: 'labels.read' },
  { key: 'players', to: '/players', icon: 'mdi-account-search-outline', requires: 'players.manage' },
  { key: 'blocklist', to: '/blocklist', icon: 'mdi-account-cancel-outline', requires: 'blocklist.manage' },
]

/** Route path prefix to the action a role needs to open it. Used by the route guard. */
export const ROUTE_REQUIREMENTS: ReadonlyArray<readonly [string, Action]> = [
  ['/pools', 'pools.read'],
  ['/snapshots', 'snapshots.read'],
  ['/jobs', 'jobs.read'],
  ['/models', 'models.read'],
  ['/labels', 'labels.read'],
  ['/players', 'players.manage'],
  ['/blocklist', 'blocklist.manage'],
]

export function requirementFor(path: string): Action | undefined {
  return ROUTE_REQUIREMENTS.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`))?.[1]
}

export function visibleNav(allowed: (a: Action) => boolean): NavItem[] {
  return NAV_ITEMS.filter(i => !i.requires || allowed(i.requires))
}
