/** RFC 9457 problem details as the control-plane API sends them. */
export interface ProblemDetails {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
  /** Validation errors (422): location only, never the submitted value. */
  errors?: Array<{ loc?: Array<string | number>, msg?: string, line?: number, [k: string]: unknown }>
  /** Rejected model transition (409): every failed quality-gate condition. */
  reasons?: string[]
  [k: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly problem: ProblemDetails

  constructor(problem: ProblemDetails) {
    super(problem.detail || problem.title)
    this.name = 'ApiError'
    this.status = problem.status
    this.problem = problem
  }

  get reasons(): string[] {
    return Array.isArray(this.problem.reasons) ? this.problem.reasons.map(String) : []
  }

  get errors(): NonNullable<ProblemDetails['errors']> {
    return Array.isArray(this.problem.errors) ? this.problem.errors : []
  }
}

export class NetworkError extends Error {
  constructor(message = 'The API could not be reached') {
    super(message)
    this.name = 'NetworkError'
  }
}

/** Builds an ApiError from a failed response; non-problem bodies are mapped to a generic problem. */
export async function problemFromResponse(res: Response): Promise<ApiError> {
  let body: unknown
  try {
    const text = await res.text()
    body = text ? JSON.parse(text) : null
  }
  catch {
    body = null
  }
  const b = typeof body === 'object' && body !== null && !Array.isArray(body) ? (body as Record<string, unknown>) : {}
  const problem: ProblemDetails = {
    ...b,
    type: typeof b.type === 'string' ? b.type : 'about:blank',
    title: typeof b.title === 'string' ? b.title : res.statusText || `HTTP ${res.status}`,
    status: typeof b.status === 'number' ? b.status : res.status,
    detail: typeof b.detail === 'string' ? b.detail : undefined,
  }
  return new ApiError(problem)
}
