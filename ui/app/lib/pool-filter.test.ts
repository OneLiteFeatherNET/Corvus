import { describe, expect, it } from 'vitest'
import { emptyPoolForm, formToPayload, payloadToForm, summarizeFilter, validatePoolForm } from './pool-filter'
import type { PoolFormState } from './pool-filter'

const filled = (): PoolFormState => ({
  name: '  Bedwars 1.21  ',
  description: 'clean sessions',
  gameContexts: ['bedwars', ' bedwars ', 'skywars', ''],
  backendServers: ['bw-1'],
  protocolMin: 767,
  protocolMax: 773,
  brandInclude: ['vanilla'],
  brandExclude: ['lunar', 'badlion'],
  dateStart: '2026-09-01',
  dateEnd: '2026-09-30',
  labelValue: 'cheat',
  labelMinConfidence: 0.8,
  minDurationS: 120,
  maxDropRate: 0.05,
  excludeOrphans: false,
  retentionDays: 365,
})

describe('formToPayload', () => {
  it('maps every control to its API field', () => {
    expect(formToPayload(filled())).toEqual({
      name: 'Bedwars 1.21',
      description: 'clean sessions',
      filter: {
        game_contexts: ['bedwars', 'skywars'],
        backend_servers: ['bw-1'],
        client_protocol: { min: 767, max: 773 },
        client_brand: { include: ['vanilla'], exclude: ['lunar', 'badlion'] },
        date_range: { start: '2026-09-01', end: '2026-09-30' },
        label: { value: 'cheat', min_confidence: 0.8 },
        min_session_duration_s: 120,
        max_drop_rate: 0.05,
        exclude_orphans: false,
        retention_days: 365,
      },
    })
  })

  it('sends no conditions for an empty form and keeps the orphan default', () => {
    expect(formToPayload({ ...emptyPoolForm(), name: 'all' }).filter).toEqual({
      game_contexts: [],
      backend_servers: [],
      client_protocol: { min: null, max: null },
      client_brand: { include: [], exclude: [] },
      date_range: { start: null, end: null },
      label: null,
      min_session_duration_s: null,
      max_drop_rate: null,
      exclude_orphans: true,
      retention_days: null,
    })
  })

  it('turns emptied number inputs into null instead of empty strings', () => {
    const form = { ...emptyPoolForm(), name: 'x', protocolMin: '' as unknown as number, maxDropRate: '' as unknown as number }
    const f = formToPayload(form).filter
    expect(f.client_protocol).toEqual({ min: null, max: null })
    expect(f.max_drop_rate).toBeNull()
  })
})

describe('payloadToForm', () => {
  it('round-trips a normalized form', () => {
    const payload = formToPayload(filled())
    expect(formToPayload(payloadToForm({ ...payload, description: payload.description }))).toEqual(payload)
  })

  it('fills missing filter parts with "no condition"', () => {
    const form = payloadToForm({ name: 'p', filter: {} })
    expect(form).toEqual({ ...emptyPoolForm(), name: 'p' })
  })
})

describe('validatePoolForm', () => {
  const ok = { ...emptyPoolForm(), name: 'p' }

  it('accepts a minimal valid form', () => {
    expect(validatePoolForm(ok)).toEqual([])
  })

  it('flags each rule the API also enforces', () => {
    expect(validatePoolForm({ ...ok, name: ' ' })).toContain('nameRequired')
    expect(validatePoolForm({ ...ok, name: 'x'.repeat(201) })).toContain('nameTooLong')
    expect(validatePoolForm({ ...ok, protocolMin: 800, protocolMax: 700 })).toContain('protocolOrder')
    expect(validatePoolForm({ ...ok, protocolMin: -1 })).toContain('protocolNegative')
    expect(validatePoolForm({ ...ok, dateStart: '2026-10-02', dateEnd: '2026-10-01' })).toContain('dateOrder')
    expect(validatePoolForm({ ...ok, maxDropRate: 1.5 })).toContain('dropRateRange')
    expect(validatePoolForm({ ...ok, labelMinConfidence: 2 })).toContain('confidenceRange')
    expect(validatePoolForm({ ...ok, minDurationS: -5 })).toContain('durationNegative')
    expect(validatePoolForm({ ...ok, retentionDays: 0 })).toContain('retentionRange')
    expect(validatePoolForm({ ...ok, retentionDays: 4000 })).toContain('retentionRange')
  })
})

describe('summarizeFilter', () => {
  it('lists only the conditions that are set', () => {
    const keys = summarizeFilter(formToPayload(filled()).filter).map(c => c.key)
    expect(keys).toEqual([
      'gameContexts', 'backendServers', 'protocol', 'brandInclude', 'brandExclude', 'dateRange', 'label', 'minDuration',
      'maxDropRate', 'retention',
    ])
    expect(summarizeFilter(formToPayload({ ...emptyPoolForm(), name: 'x' }).filter)).toEqual([])
  })
})
