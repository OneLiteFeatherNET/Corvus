export function formatDateTime(iso: string | null | undefined, locale = 'de'): string {
  if (!iso) return '–'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(d)
}

export function shortId(id: string): string {
  return id.slice(0, 8)
}

export function formatNumber(n: number | null | undefined, locale = 'de'): string {
  return n === null || n === undefined ? '–' : new Intl.NumberFormat(locale).format(n)
}

/** Count of a first page: "200+" when more pages exist. */
export function countLabel(count: number, hasMore: boolean): string {
  return hasMore ? `${count}+` : String(count)
}

export function stageColor(stage: string): string {
  switch (stage) {
    case 'enforce': return 'primary'
    case 'canary': return 'secondary'
    case 'shadow': return 'tertiary'
    default: return 'default'
  }
}

export function stageIcon(stage: string): string {
  switch (stage) {
    case 'enforce': return 'mdi-shield-check'
    case 'canary': return 'mdi-bird'
    case 'shadow': return 'mdi-ghost-outline'
    case 'archived': return 'mdi-archive-outline'
    default: return 'mdi-tray-full'
  }
}

export function jobColor(status: string): string {
  switch (status) {
    case 'succeeded': return 'primary'
    case 'running': return 'tertiary'
    case 'failed': return 'error'
    default: return 'default'
  }
}

export function jobIcon(status: string): string {
  switch (status) {
    case 'succeeded': return 'mdi-check-circle-outline'
    case 'running': return 'mdi-progress-clock'
    case 'failed': return 'mdi-alert-circle-outline'
    default: return 'mdi-timer-sand'
  }
}

export type ChipTone = 'neutral' | 'primary' | 'secondary' | 'tertiary' | 'error'

/** Maps the colour names of stageColor and jobColor to a chip tone. */
export function chipTone(color: string): ChipTone {
  return color === 'primary' || color === 'secondary' || color === 'tertiary' || color === 'error' ? color : 'neutral'
}
