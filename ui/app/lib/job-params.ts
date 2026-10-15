export const JOB_PARAMS_MAX_BYTES = 16 * 1024

export type JobParamsResult =
  | { ok: true, value: Record<string, unknown> }
  | { ok: false, error: 'invalidJson' | 'notObject' | 'tooLarge' }

/** Parses the free-form job parameters. The API caps them at 16 KiB and forbids secrets (not checkable here). */
export function parseJobParams(text: string): JobParamsResult {
  const t = text.trim()
  if (!t) return { ok: true, value: {} }
  if (new TextEncoder().encode(t).length > JOB_PARAMS_MAX_BYTES) return { ok: false, error: 'tooLarge' }
  let parsed: unknown
  try {
    parsed = JSON.parse(t)
  }
  catch {
    return { ok: false, error: 'invalidJson' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return { ok: false, error: 'notObject' }
  return { ok: true, value: parsed as Record<string, unknown> }
}
