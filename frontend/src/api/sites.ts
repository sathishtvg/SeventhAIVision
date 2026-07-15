import type { Site } from '@/types/api'
import { apiClient } from './client'

export const getSites = (isActive?: boolean) =>
  apiClient
    .get<Site[]>('/api/v1/sites', { params: isActive !== undefined ? { is_active: isActive } : {} })
    .then((r) => r.data)

export const getSite = (siteId: string) =>
  apiClient.get<Site>(`/api/v1/sites/${siteId}`).then((r) => r.data)

export const createSite = (data: {
  name: string
  address?: string
  description?: string
  latitude?: number
  longitude?: number
  geofence_radius_meters?: number
  client_id?: string
  bill_rate?: number
}) => apiClient.post<Site>('/api/v1/sites', data).then((r) => r.data)

export const updateSite = (
  siteId: string,
  data: {
    name?: string
    address?: string
    description?: string
    latitude?: number
    longitude?: number
    geofence_radius_meters?: number
    is_active?: boolean
    client_id?: string
    bill_rate?: number
  }
) => apiClient.put(`/api/v1/sites/${siteId}`, data).then((r) => r.data)

export const deactivateSite = (siteId: string) =>
  apiClient.delete(`/api/v1/sites/${siteId}`).then((r) => r.data)

export const getSiteCameras = (siteId: string) =>
  apiClient.get(`/api/v1/sites/${siteId}/cameras`).then((r) => r.data)

export const validateStream = (data: { url: string; username?: string; password?: string }) =>
  apiClient.post('/api/v1/cameras/validate-stream', data).then((r) => r.data)
