import {
  blocklistResource, jobsResource, labelsResource, modelsResource, playersResource, poolsResource, snapshotsResource,
} from '~/lib/resources'

// One composable per resource; each returns the typed calls bound to the shared API client.
const api = () => useNuxtApp().$corvus.api

export const usePools = () => poolsResource(api())
export const useSnapshots = () => snapshotsResource(api())
export const useJobs = () => jobsResource(api())
export const useModels = () => modelsResource(api())
export const useLabels = () => labelsResource(api())
export const usePlayers = () => playersResource(api())
export const useBlocklist = () => blocklistResource(api())
