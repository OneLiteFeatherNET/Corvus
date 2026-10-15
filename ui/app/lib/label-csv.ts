export const LABEL_CSV_REQUIRED = ['subject_type', 'subject', 'label', 'source', 'confidence'] as const
export const LABEL_CSV_OPTIONAL = ['valid_from', 'valid_to', 'note'] as const
export const LABEL_CSV_MAX_ROWS = 50_000
export const LABEL_CSV_MAX_BYTES = 8 * 1024 * 1024

const SUBJECT_TYPES = ['pseudonym', 'session']
const LABELS = ['legit', 'cheat']
const SOURCES = ['honeypot', 'staff_verdict', 'ban_import', 'weak_rule']
const HEX = /^[0-9a-f]+$/i

export interface CsvRowIssue {
  /** 1-based line number in the file, header = line 1. */
  line: number
  code: string
  column?: string
}

export interface CsvPreview {
  header: string[]
  rows: string[][]
  rowCount: number
  issues: CsvRowIssue[]
  /** True when nothing blocks the upload. The server validates again and stays authoritative. */
  valid: boolean
}

/** Minimal RFC 4180 parser (quoted fields, doubled quotes, CRLF). */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false
  const src = text.charCodeAt(0) === 0xFEFF ? text.slice(1) : text
  for (let i = 0; i < src.length; i++) {
    const c = src[i]
    if (inQuotes) {
      if (c === '"') {
        if (src[i + 1] === '"') {
          field += '"'
          i++
        }
        else inQuotes = false
      }
      else field += c
    }
    else if (c === '"') inQuotes = true
    else if (c === ',') {
      row.push(field)
      field = ''
    }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++
      row.push(field)
      field = ''
      rows.push(row)
      row = []
    }
    else field += c
  }
  if (field !== '' || row.length > 0) {
    row.push(field)
    rows.push(row)
  }
  return rows.filter(r => !(r.length === 1 && r[0] === ''))
}

function isIsoDateTimeWithZone(v: string): boolean {
  return /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/.test(v) && !Number.isNaN(Date.parse(v))
}

/** Client-side check of a label import file; mirrors the documented server rules for an early preview. */
export function validateLabelCsv(text: string, byteLength = text.length): CsvPreview {
  const issues: CsvRowIssue[] = []
  if (byteLength > LABEL_CSV_MAX_BYTES) issues.push({ line: 0, code: 'tooLarge' })

  const parsed = parseCsv(text)
  const header = (parsed[0] ?? []).map(h => h.trim())
  const body = parsed.slice(1)

  if (parsed.length === 0) {
    issues.push({ line: 1, code: 'empty' })
    return { header, rows: [], rowCount: 0, issues, valid: false }
  }
  for (const col of LABEL_CSV_REQUIRED) {
    if (!header.includes(col)) issues.push({ line: 1, code: 'missingColumn', column: col })
  }
  for (const col of header) {
    if (![...LABEL_CSV_REQUIRED, ...LABEL_CSV_OPTIONAL].includes(col as never)) {
      issues.push({ line: 1, code: 'unknownColumn', column: col })
    }
  }
  if (body.length > LABEL_CSV_MAX_ROWS) issues.push({ line: 0, code: 'tooManyRows' })

  const idx = (name: string) => header.indexOf(name)
  if (!issues.some(i => i.code === 'missingColumn')) {
    body.forEach((cells, n) => {
      const line = n + 2
      const get = (name: string) => (cells[idx(name)] ?? '').trim()
      if (cells.length > header.length) issues.push({ line, code: 'extraCells' })
      const type = get('subject_type')
      const subject = get('subject')
      if (!SUBJECT_TYPES.includes(type)) issues.push({ line, code: 'badSubjectType', column: 'subject_type' })
      else {
        const want = type === 'pseudonym' ? 64 : 32
        if (subject.length !== want || !HEX.test(subject)) issues.push({ line, code: 'badSubject', column: 'subject' })
      }
      if (!LABELS.includes(get('label'))) issues.push({ line, code: 'badLabel', column: 'label' })
      if (!SOURCES.includes(get('source'))) issues.push({ line, code: 'badSource', column: 'source' })
      const conf = get('confidence')
      const c = Number(conf)
      if (conf === '' || Number.isNaN(c) || c < 0 || c > 1) issues.push({ line, code: 'badConfidence', column: 'confidence' })
      const from = get('valid_from')
      const to = get('valid_to')
      if (from && !isIsoDateTimeWithZone(from)) issues.push({ line, code: 'badDate', column: 'valid_from' })
      if (to && !isIsoDateTimeWithZone(to)) issues.push({ line, code: 'badDate', column: 'valid_to' })
      if (from && to && isIsoDateTimeWithZone(from) && isIsoDateTimeWithZone(to) && Date.parse(from) >= Date.parse(to)) {
        issues.push({ line, code: 'dateOrder', column: 'valid_from' })
      }
      if (get('note').length > 2000) issues.push({ line, code: 'noteTooLong', column: 'note' })
    })
  }

  return { header, rows: body, rowCount: body.length, issues, valid: issues.length === 0 && body.length > 0 }
}
