import type { Incident } from '@/types/api'
import { apiClient } from './client'

export interface IncidentsPage { items: Incident[]; has_more: boolean }

export const getIncidents = (status?: string, siteId?: string, moduleType?: string, limit = 50, offset = 0) =>
  apiClient
    .get<IncidentsPage>('/api/v1/incidents', {
      params: { status_filter: status, site_id: siteId, module_type: moduleType, limit, offset },
    })
    .then((r) => r.data)

export const addIncidentNote = (incidentId: string, note: string) =>
  apiClient.post(`/api/v1/incidents/${incidentId}/notes`, { note }).then((r) => r.data)

export const assignIncident = (incidentId: string, userId: string) =>
  apiClient.post(`/api/v1/incidents/${incidentId}/assign`, { assigned_to_user_id: userId }).then((r) => r.data)

export const resolveIncident = (incidentId: string) =>
  apiClient.post(`/api/v1/incidents/${incidentId}/resolve`).then((r) => r.data)

export const updateIncidentStatus = (
  incidentId: string,
  status: string,
  notes?: string,
  latitude?: number,
  longitude?: number,
) =>
  apiClient
    .put(`/api/v1/incidents/${incidentId}/status`, { status, notes, latitude, longitude })
    .then((r) => r.data)

export const getIncidentTimeline = (incidentId: string) =>
  apiClient.get(`/api/v1/incidents/${incidentId}/timeline`).then((r) => r.data)

export interface BulkResult { updated: number; skipped: number }

export const bulkResolveIncidents = (ids: string[]) =>
  apiClient.post<BulkResult>('/api/v1/incidents/bulk-resolve', { ids }).then((r) => r.data)

export const bulkUpdateIncidentStatus = (ids: string[], status: string) =>
  apiClient.post<BulkResult>('/api/v1/incidents/bulk-update-status', { ids, status }).then((r) => r.data)
