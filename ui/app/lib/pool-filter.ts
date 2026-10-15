import type { PoolFilter, PoolInput } from '~/types'

export type LabelValue = '' | 'legit' | 'cheat' | 'labeled' | 'unlabeled'

/** Flat form state behind the filter builder. Empty strings and nulls mean "no condition". */
export interface PoolFormState {
  name: string
  description: string
  gameContexts: string[]
  backendServers: string[]
  protocolMin: number | null
  protocolMax: number | null
  brandInclude: string[]
  brandExclude: string[]
  /** ISO date (YYYY-MM-DD) or empty. */
  dateStart: string
  dateEnd: string
  labelValue: LabelValue
  labelMinConfidence: number
  minDurationS: number | null
  maxDropRate: number | null
  excludeOrphans: boolean
  retentionDays: number | null
}

export type PoolFormError =
  | 'nameRequired'
  | 'nameTooLong'
  | 'protocolOrder'
  | 'dateOrder'
  | 'dropRateRange'
  | 'confidenceRange'
  | 'durationNegative'
  | 'retentionRange'
  | 'protocolNegative'

export function emptyPoolForm(): PoolFormState {
  return {
    name: '',
    description: '',
    gameContexts: [],
    backendServers: [],
    protocolMin: null,
    protocolMax: null,
    brandInclude: [],
    brandExclude: [],
    dateStart: '',
    dateEnd: '',
    labelValue: '',
    labelMinConfidence: 0,
    minDurationS: null,
    maxDropRate: null,
    excludeOrphans: true,
    retentionDays: null,
  }
}

function clean(list: string[]): string[] {
  const seen = new Set<string>()
  for (const raw of list) {
    const v = raw.trim()
    if (v) seen.add(v)
  }
  return [...seen]
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

/** Text inputs hand back '' for an emptied number field; this turns those into proper nulls. */
export function normalizeForm(form: PoolFormState): PoolFormState {
  return {
    ...form,
    protocolMin: num(form.protocolMin),
    protocolMax: num(form.protocolMax),
    minDurationS: num(form.minDurationS),
    maxDropRate: num(form.maxDropRate),
    retentionDays: num(form.retentionDays),
    labelMinConfidence: num(form.labelMinConfidence) ?? 0,
  }
}

/** Form state to the request body of POST/PUT /pools. */
export function formToPayload(raw: PoolFormState): PoolInput & { filter: PoolFilter } {
  const form = normalizeForm(raw)
  const filter: PoolFilter = {
    game_contexts: clean(form.gameContexts),
    backend_servers: clean(form.backendServers),
    client_protocol: { min: form.protocolMin, max: form.protocolMax },
    client_brand: { include: clean(form.brandInclude), exclude: clean(form.brandExclude) },
    date_range: { start: form.dateStart || null, end: form.dateEnd || null },
    label: form.labelValue ? { value: form.labelValue, min_confidence: form.labelMinConfidence } : null,
    min_session_duration_s: form.minDurationS,
    max_drop_rate: form.maxDropRate,
    exclude_orphans: form.excludeOrphans,
    retention_days: form.retentionDays,
  }
  return { name: form.name.trim(), description: form.description.trim(), filter }
}

/** A stored pool to form state; missing parts fall back to "no condition". */
export function payloadToForm(pool: { name: string, description?: string, filter?: Partial<PoolFilter> }): PoolFormState {
  const f: Partial<PoolFilter> = pool.filter ?? {}
  return {
    name: pool.name,
    description: pool.description ?? '',
    gameContexts: [...(f.game_contexts ?? [])],
    backendServers: [...(f.backend_servers ?? [])],
    protocolMin: f.client_protocol?.min ?? null,
    protocolMax: f.client_protocol?.max ?? null,
    brandInclude: [...(f.client_brand?.include ?? [])],
    brandExclude: [...(f.client_brand?.exclude ?? [])],
    dateStart: f.date_range?.start ?? '',
    dateEnd: f.date_range?.end ?? '',
    labelValue: f.label?.value ?? '',
    labelMinConfidence: f.label?.min_confidence ?? 0,
    minDurationS: f.min_session_duration_s ?? null,
    maxDropRate: f.max_drop_rate ?? null,
    excludeOrphans: f.exclude_orphans ?? true,
    retentionDays: f.retention_days ?? null,
  }
}

/** Mirrors the server-side constraints so most mistakes show up before the request. */
export function validatePoolForm(raw: PoolFormState): PoolFormError[] {
  const form = normalizeForm(raw)
  const errors: PoolFormError[] = []
  const name = form.name.trim()
  if (!name) errors.push('nameRequired')
  if (name.length > 200) errors.push('nameTooLong')
  if ((form.protocolMin ?? 0) < 0 || (form.protocolMax ?? 0) < 0) errors.push('protocolNegative')
  if (form.protocolMin !== null && form.protocolMax !== null && form.protocolMin > form.protocolMax) {
    errors.push('protocolOrder')
  }
  if (form.dateStart && form.dateEnd && form.dateStart > form.dateEnd) errors.push('dateOrder')
  if (form.maxDropRate !== null && (form.maxDropRate < 0 || form.maxDropRate > 1)) errors.push('dropRateRange')
  if (form.labelMinConfidence < 0 || form.labelMinConfidence > 1) errors.push('confidenceRange')
  if (form.minDurationS !== null && form.minDurationS < 0) errors.push('durationNegative')
  if (form.retentionDays !== null && (form.retentionDays < 1 || form.retentionDays > 3650)) {
    errors.push('retentionRange')
  }
  return errors
}

/** Short human-readable chips for a stored filter (list and detail views). */
export function summarizeFilter(f: PoolFilter): Array<{ key: string, value: string }> {
  const out: Array<{ key: string, value: string }> = []
  if (f.game_contexts?.length) out.push({ key: 'gameContexts', value: f.game_contexts.join(', ') })
  if (f.backend_servers?.length) out.push({ key: 'backendServers', value: f.backend_servers.join(', ') })
  const p = f.client_protocol
  if (p && (p.min != null || p.max != null)) out.push({ key: 'protocol', value: `${p.min ?? '*'}–${p.max ?? '*'}` })
  if (f.client_brand?.include?.length) out.push({ key: 'brandInclude', value: f.client_brand.include.join(', ') })
  if (f.client_brand?.exclude?.length) out.push({ key: 'brandExclude', value: f.client_brand.exclude.join(', ') })
  const d = f.date_range
  if (d && (d.start || d.end)) out.push({ key: 'dateRange', value: `${d.start ?? '*'} – ${d.end ?? '*'}` })
  if (f.label) out.push({ key: 'label', value: `${f.label.value} ≥ ${f.label.min_confidence ?? 0}` })
  if (f.min_session_duration_s != null) out.push({ key: 'minDuration', value: `${f.min_session_duration_s}s` })
  if (f.max_drop_rate != null) out.push({ key: 'maxDropRate', value: String(f.max_drop_rate) })
  if (f.retention_days != null) out.push({ key: 'retention', value: `${f.retention_days}d` })
  return out
}
