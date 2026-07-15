import type { Alert } from '@/types/api'
import { apiClient } from './client'

export interface AlertsPage { items: Alert[]; has_more: boolean }

export const getAlerts = (status?: string, siteId?: string, moduleType?: string, limit = 50, offset = 0) =>
  apiClient
    .get<AlertsPage>('/api/v1/alerts', {
      params: {
        status_filter: status,
        site_id: siteId,
        module_type: moduleType,
        limit,
        offset,
      },
    })
    .then((r) => r.data)

export const acknowledgeAlert = (alertId: string) =>
  apiClient.post(`/api/v1/alerts/${alertId}/acknowledge`).then((r) => r.data)

export const markFalsePositive = (alertId: string, fpReason?: string) =>
  apiClient
    .post(`/api/v1/alerts/${alertId}/false-positive`, { fp_reason: fpReason })
    .then((r) => r.data)

/** Singular resolve — reuses the existing 'dismissed' status. */
export const dismissAlert = (alertId: string) =>
  apiClient.post(`/api/v1/alerts/${alertId}/dismiss`).then((r) => r.data)

export interface EscalationTarget {
  user_id: string
  full_name: string | null
  role_id: number
}

export const getEscalationTargets = (alertId: string) =>
  apiClient.get<EscalationTarget[]>(`/api/v1/alerts/${alertId}/escalation-targets`).then((r) => r.data)

export interface BulkResult { updated: number; skipped: number }

export const bulkAcknowledgeAlerts = (ids: string[]) =>
  apiClient.post<BulkResult>('/api/v1/alerts/bulk-acknowledge', { ids }).then((r) => r.data)

export const bulkDismissAlerts = (ids: string[]) =>
  apiClient.post<BulkResult>('/api/v1/alerts/bulk-dismiss', { ids }).then((r) => r.data)

// Alert assignment
export const assignAlert = (alertId: string, userId: string) =>
  apiClient.post(`/api/v1/alerts/${alertId}/assign`, { assigned_to_user_id: userId }).then((r) => r.data)

export const unassignAlert = (alertId: string) =>
  apiClient.post(`/api/v1/alerts/${alertId}/unassign`).then((r) => r.data)

export const bulkAssignAlerts = (ids: string[], userId: string) =>
  apiClient.post<BulkResult>('/api/v1/alerts/bulk-assign', { ids, assigned_to_user_id: userId }).then((r) => r.data)

export const bulkUnassignAlerts = (ids: string[]) =>
  apiClient.post<BulkResult>('/api/v1/alerts/bulk-unassign', { ids }).then((r) => r.data)

// Alert notes
export interface AlertNote {
  id: string
  alert_id: string
  author_user_id: string | null
  author_name: string | null
  author_email: string | null
  note: string
  created_at: string
}

export const getAlertNotes = (alertId: string) =>
  apiClient.get<AlertNote[]>(`/api/v1/alerts/${alertId}/notes`).then((r) => r.data)

export const addAlertNote = (alertId: string, note: string) =>
  apiClient.post<AlertNote>(`/api/v1/alerts/${alertId}/notes`, { note }).then((r) => r.data)

