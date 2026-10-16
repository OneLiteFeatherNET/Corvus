/** API enum values (see openapi.json); every value needs a translation under the given i18n prefix. */
export const ENUMS = {
  'jobs.type': ['build_features', 'evaluate', 'train'],
  'jobs.status': ['queued', 'running', 'succeeded', 'failed'],
  'models.stage': ['registered', 'shadow', 'canary', 'enforce', 'archived'],
  'blocklist.kinds': ['block', 'allow'],
  'blocklist.sources': ['manual', 'erasure'],
} as const satisfies Record<string, readonly string[]>

/** "build_features" -> "Build features": shown for a value the interface has no translation for. */
export function humanize(value: string): string {
  const s = value.replace(/[_-]+/g, ' ').trim()
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : value
}

export function enumLabel(prefix: string, value: string, t: (key: string) => string, te: (key: string) => boolean): string {
  const key = `${prefix}.${value}`
  return te(key) ? t(key) : humanize(value)
}
