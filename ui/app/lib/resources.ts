import type { ApiClient } from './api-client'
import type {
  BlockEntry, BlockInput, Erasure, Job, JobInput, JobState, Label, LabelInput, Model, ModelTransition,
  Paged, PlayerLookup, Pool, PoolInput, Preview, Snapshot, Stage,
} from '~/types'

export interface PageQuery {
  limit?: number
  cursor?: string | null
}

const q = (p: PageQuery = {}) => ({ limit: p.limit, cursor: p.cursor })

export function poolsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery) => c.get<Paged<Pool>>('/pools', { query: q(p) }),
    get: (id: string) => c.get<Pool>(`/pools/${id}`),
    create: (body: PoolInput) => c.post<Pool>('/pools', { body }),
    update: (id: string, body: PoolInput) => c.put<Pool>(`/pools/${id}`, { body }),
    remove: (id: string) => c.delete(`/pools/${id}`),
    preview: (id: string) => c.post<Preview>(`/pools/${id}/preview`),
    createSnapshot: (id: string) => c.post<Snapshot>(`/pools/${id}/snapshots`),
    snapshots: (id: string, p?: PageQuery) => c.get<Paged<Snapshot>>(`/pools/${id}/snapshots`, { query: q(p) }),
  }
}

export function snapshotsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery) => c.get<Paged<Snapshot>>('/snapshots', { query: q(p) }),
    get: (id: string) => c.get<Snapshot>(`/snapshots/${id}`),
  }
}

export function jobsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery & { status?: JobState | '' }) =>
      c.get<Paged<Job>>('/jobs', { query: { ...q(p), status: p?.status } }),
    get: (id: string) => c.get<Job>(`/jobs/${id}`),
    create: (body: JobInput) => c.post<Job>('/jobs', { body }),
  }
}

export function modelsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery & { stage?: Stage | '' }) =>
      c.get<Paged<Model>>('/models', { query: { ...q(p), stage: p?.stage } }),
    get: (id: string) => c.get<Model>(`/models/${id}`),
    transitions: (id: string) => c.get<ModelTransition[]>(`/models/${id}/transitions`),
    setStage: (id: string, stage: Stage, reason: string) =>
      c.post<Model>(`/models/${id}/stage`, { body: { stage, reason } }),
  }
}

export function labelsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery & { subjectType?: string, subject?: string }) =>
      c.get<Paged<Label>>('/labels', { query: { ...q(p), subject_type: p?.subjectType, subject: p?.subject } }),
    create: (body: LabelInput) => c.post<Label>('/labels', { body }),
    importCsv: (csv: string) =>
      c.post<{ imported: number }>('/labels/import', { rawBody: { content: csv, contentType: 'text/csv' } }),
  }
}

export function playersResource(c: ApiClient) {
  return {
    /** POST on purpose: the UUID must not appear in a URL or an access log. */
    lookup: (uuid: string) => c.post<PlayerLookup>('/players/lookup', { body: { uuid } }),
    erase: (target: { uuid?: string, pseudonym?: string }) => c.post<Erasure>('/players/erasure', { body: target }),
    erasure: (id: string) => c.get<Erasure>(`/players/erasure/${id}`),
  }
}

export function blocklistResource(c: ApiClient) {
  return {
    list: (p?: PageQuery) => c.get<Paged<BlockEntry>>('/blocklist', { query: q(p) }),
    put: (pseudonym: string, body: BlockInput) => c.put<BlockEntry>(`/blocklist/${pseudonym}`, { body }),
    remove: (pseudonym: string) => c.delete(`/blocklist/${pseudonym}`),
  }
}

/**
 * The API has no lookup by name and version, so the registry is scanned page by page. The registry is small
 * (tens of models); `maxPages` bounds the work.
 */
export async function findModel(
  models: ReturnType<typeof modelsResource>,
  name: string,
  version: string,
  maxPages = 10,
): Promise<Model | null> {
  let cursor: string | null | undefined
  for (let i = 0; i < maxPages; i++) {
    const page = await models.list({ limit: 200, cursor })
    const hit = page.items.find(m => m.name === name && m.version === version)
    if (hit) return hit
    if (!page.next_cursor) return null
    cursor = page.next_cursor
  }
  return null
}
