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
  late_grace_minutes?: number
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
  late_grace_minutes?: number
    is_active?: boolean
    client_id?: string
    bill_rate?: number
    // VMS config is update-only: the cameras have to exist and be assigned to
    // the site before they can be bound to its entry/exit lanes.
    //
    // These three are `| null` rather than optional-undefined on purpose —
    // the backend keys off which fields are PRESENT in the request, so sending
    // an explicit null is the only way to UNBIND a camera or clear the parking
    // allowance. Omitting them leaves the stored value untouched.
    vms_enabled?: boolean
    entry_lpr_camera_id?: string | null
    exit_lpr_camera_id?: string | null
    free_parking_minutes?: number | null
  }
) => apiClient.put(`/api/v1/sites/${siteId}`, data).then((r) => r.data)

export const deactivateSite = (siteId: string) =>
  apiClient.delete(`/api/v1/sites/${siteId}`).then((r) => r.data)

export const getSiteCameras = (siteId: string) =>
  apiClient.get(`/api/v1/sites/${siteId}/cameras`).then((r) => r.data)

export const validateStream = (data: { url: string; username?: string; password?: string }) =>
  apiClient.post('/api/v1/cameras/validate-stream', data).then((r) => r.data)
