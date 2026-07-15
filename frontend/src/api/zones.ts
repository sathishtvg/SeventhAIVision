import type { CrowdZone, RestrictedZone } from '@/types/api'
import { apiClient } from './client'

// Restricted zones (intrusion detection)
export const getZones = () =>
  apiClient.get<RestrictedZone[]>('/api/v1/zones').then((r) => r.data)

export const createZone = (data: {
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  severity?: string
  applies_to_modules?: string[]
}) => apiClient.post('/api/v1/zones', data).then((r) => r.data)

export const deleteZone = (zoneId: string) =>
  apiClient.delete(`/api/v1/zones/${zoneId}`).then((r) => r.data)

export const bulkBypassZones = (ids: string[], bypass_minutes: number) =>
  apiClient.post('/api/v1/zones/bulk-bypass', { ids, bypass_minutes }).then((r) => r.data)

export const bulkRestoreZones = (ids: string[]) =>
  apiClient.post('/api/v1/zones/bulk-restore', { ids }).then((r) => r.data)

export const setZoneSchedule = (
  zoneId: string,
  data: {
    enabled: boolean
    timezone: string
    active_days: number[]
    active_start_time: string
    active_end_time: string
  },
) => apiClient.put(`/api/v1/zones/${zoneId}/schedule`, data).then((r) => r.data)

// Crowd zones (crowd density monitoring)
export const getCrowdZones = () =>
  apiClient.get<CrowdZone[]>('/api/v1/crowd-zones').then((r) => r.data)

export const createCrowdZone = (data: {
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  max_capacity?: number
  severity?: string
}) => apiClient.post<CrowdZone>('/api/v1/crowd-zones', data).then((r) => r.data)

export const updateCrowdZone = (id: string, data: {
  name?: string
  max_capacity?: number
  severity?: string
  is_active?: boolean
}) => apiClient.put<CrowdZone>(`/api/v1/crowd-zones/${id}`, data).then((r) => r.data)

export const deleteCrowdZone = (zoneId: string) =>
  apiClient.delete(`/api/v1/crowd-zones/${zoneId}`).then((r) => r.data)
