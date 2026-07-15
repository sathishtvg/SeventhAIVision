import { apiClient } from './client'

export type AlertSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type AlertStatus = 'open' | 'acknowledged' | 'resolved' | 'dismissed'

export interface Alert {
  id: string
  camera_id: string
  camera_name?: string
  site_name?: string
  module_type: string
  severity: AlertSeverity
  alert_code: string | null
  title: string
  message: string | null
  status: AlertStatus
  acknowledged_at: string | null
  acknowledged_by_name?: string | null
  acknowledged_via?: 'web' | 'mobile' | null
  fp_reason?: string | null
  fp_marked_at?: string | null
  fp_marked_by_name?: string | null
  fp_marked_via?: 'web' | 'mobile' | null
  created_at: string
}

export const getAlerts = (status?: string, siteId?: string, moduleType?: string) => {
  const params: Record<string, string> = {}
  if (status) params.status = status
  if (siteId) params.site_id = siteId
  if (moduleType) params.module_type = moduleType
  return apiClient.get<Alert[]>('/api/v1/alerts', { params }).then((r) => r.data)
}

// Responses from this app are tagged 'mobile' so a supervisor reviewing the
// alert log can tell a guard's in-field phone response from a desk response.
export const acknowledgeAlert = (id: string) =>
  apiClient.post(`/api/v1/alerts/${id}/acknowledge`, { via: 'mobile' }).then((r) => r.data)

export const markFalsePositive = (id: string, fpReason?: string) =>
  apiClient.post(`/api/v1/alerts/${id}/false-positive`, { fp_reason: fpReason, via: 'mobile' }).then((r) => r.data)
