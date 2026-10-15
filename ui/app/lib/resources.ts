import type { ApiClient } from './api-client'
import type {
  BlockEntry, BlockInput, Erasure, Job, JobInput, JobState, Label, LabelInput, Me, Model, ModelTransition,
  Paged, PlayerLookup, Pool, PoolFilter, PoolInput, Preview, ScoreHistory, ScoreLatest, Snapshot, Stage, Summary,
} from '~/types'

export interface PageQuery {
  limit?: number
  cursor?: string | null
}

const q = (p: PageQuery = {}) => ({ limit: p.limit, cursor: p.cursor })

/** Server-side model filters; exact match, combined with AND. Empty values are not sent. */
export interface ModelFilter {
  name?: string
  version?: string
  gameContext?: string
  domain?: string
  stage?: Stage | ''
}

export function poolsResource(c: ApiClient) {
  return {
    list: (p?: PageQuery) => c.get<Paged<Pool>>('/pools', { query: q(p) }),
    get: (id: string) => c.get<Pool>(`/pools/${id}`),
    create: (body: PoolInput) => c.post<Pool>('/pools', { body }),
    update: (id: string, body: PoolInput) => c.put<Pool>(`/pools/${id}`, { body }),
    remove: (id: string) => c.delete(`/pools/${id}`),
    preview: (id: string) => c.post<Preview>(`/pools/${id}/preview`),
    /** Live preview of an unsaved filter; nothing is stored. */
    previewFilter: (filter: PoolFilter, signal?: AbortSignal) =>
      c.post<Preview>('/pools/preview', { body: filter, signal }),
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
    list: (p?: PageQuery & ModelFilter) =>
      c.get<Paged<Model>>('/models', {
        query: { ...q(p), name: p?.name, version: p?.version, game_context: p?.gameContext, domain: p?.domain, stage: p?.stage },
      }),
    get: (id: string) => c.get<Model>(`/models/${id}`),
    /** One model by its unique name and version. 404 is an ApiError. */
    byRef: (name: string, version: string) =>
      c.get<Model>(`/models/${encodeURIComponent(name)}/${encodeURIComponent(version)}`),
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
    /** Admin list of all erasure requests, newest first. */
    erasureRequests: (p?: PageQuery & { status?: string }) =>
      c.get<Paged<Erasure>>('/erasure-requests', { query: { ...q(p), status: p?.status } }),
  }
}

export function meResource(c: ApiClient) {
  return { get: (signal?: AbortSignal) => c.get<Me>('/me', { signal }) }
}

export function summaryResource(c: ApiClient) {
  return { get: () => c.get<Summary>('/summary') }
}

export function scoresResource(c: ApiClient) {
  return {
    latest: (pseudonym: string, signal?: AbortSignal) =>
      c.get<{ items: ScoreLatest[] }>(`/scores/players/${pseudonym}`, { signal }),
    history: (pseudonym: string, p?: { gameContext?: string, limit?: number }, signal?: AbortSignal) =>
      c.get<{ items: ScoreHistory[] }>(`/scores/players/${pseudonym}/history`, {
        query: { game_context: p?.gameContext, limit: p?.limit },
        signal,
      }),
  }
}

export function blocklistResource(c: ApiClient) {
  return {
    list: (p?: PageQuery) => c.get<Paged<BlockEntry>>('/blocklist', { query: q(p) }),
    put: (pseudonym: string, body: BlockInput) => c.put<BlockEntry>(`/blocklist/${pseudonym}`, { body }),
    remove: (pseudonym: string) => c.delete(`/blocklist/${pseudonym}`),
  }
}
