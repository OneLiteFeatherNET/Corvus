import type { components } from './api'

// Aliases over the generated OpenAPI types (app/types/api.d.ts, `pnpm api:types`).
type S = components['schemas']

export type Pool = S['PoolOut']
export type PoolInput = S['PoolIn']
export type PoolFilter = S['PoolFilter']
export type Preview = S['PreviewOut']
export type Snapshot = S['SnapshotOut']
export type Label = S['LabelOut']
export type LabelInput = S['LabelIn']
export type PlayerLookup = S['PlayerLookupOut']
export type Erasure = S['ErasureOut']
export type BlockEntry = S['BlockEntryOut']
export type BlockInput = S['BlockIn']
export type Model = S['ModelOut']
export type ModelTransition = S['TransitionOut']
export type Job = S['JobOut']
export type JobInput = S['JobIn']
export type JobType = S['JobType']
export type JobState = S['JobState']

export type Role = 'admin' | 'data_scientist' | 'staff' | 'viewer'
export type Stage = 'registered' | 'shadow' | 'canary' | 'enforce' | 'archived'

export interface Paged<T> {
  items: T[]
  next_cursor: string | null
}
