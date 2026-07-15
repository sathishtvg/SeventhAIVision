import { apiClient } from './client'

export type ZoneSeverity = 'low' | 'medium' | 'high' | 'critical'

export interface RestrictedZone {
  id: string
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  severity: ZoneSeverity
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface CrowdZone {
  id: string
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  max_capacity: number
  severity: ZoneSeverity
  is_active: boolean
  created_at: string
  updated_at: string
}

// Restricted zones (intrusion detection)
export const getZones = () =>
  apiClient.get<RestrictedZone[]>('/api/v1/zones').then((r) => r.data)

export const createZone = (data: {
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  severity?: ZoneSeverity
}) => apiClient.post<RestrictedZone>('/api/v1/zones', data).then((r) => r.data)

export const deleteZone = (zoneId: string) =>
  apiClient.delete(`/api/v1/zones/${zoneId}`)

// Crowd zones
export const getCrowdZones = () =>
  apiClient.get<CrowdZone[]>('/api/v1/crowd-zones').then((r) => r.data)

export const createCrowdZone = (data: {
  camera_id: string
  name: string
  polygon: Array<{ x: number; y: number }>
  max_capacity?: number
  severity?: ZoneSeverity
}) => apiClient.post<CrowdZone>('/api/v1/crowd-zones', data).then((r) => r.data)

export const updateCrowdZone = (
  id: string,
  data: { name?: string; max_capacity?: number; severity?: ZoneSeverity; is_active?: boolean },
) => apiClient.put<CrowdZone>(`/api/v1/crowd-zones/${id}`, data).then((r) => r.data)

export const deleteCrowdZone = (zoneId: string) =>
  apiClient.delete(`/api/v1/crowd-zones/${zoneId}`)
