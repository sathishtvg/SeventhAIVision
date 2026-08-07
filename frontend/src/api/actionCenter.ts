import { apiClient } from './client'
import type { AlertSummary } from '@/components/common/AlertResponseDialog'

export interface ActionItem {
  id: string
  category:
    | 'contact_guard' | 'ack_alert' | 'overdue_checkpoint' | 'pending_approval' | 'camera_offline'
    | 'geofence_flag'
    | 'check_in' | 'patrol_due' | 'respond_incident' | 'doc_expiry'
  severity: 'critical' | 'high' | 'medium' | 'low'
  title: string
  subtitle: string
  action_route?: string | null
  phone?: string | null
  entity_id: string
  /** Present on `ack_alert` items only — everything the response dialog needs
   * to act on the alert in place, so the row doesn't have to navigate away. */
  alert?: AlertSummary | null
}

export interface ActionCenterResponse {
  summary: { total: number; critical: number; high: number; medium: number; low: number }
  items: ActionItem[]
}

export const getActionCenter = (): Promise<ActionCenterResponse> =>
  apiClient.get<ActionCenterResponse>('/api/v1/action-center').then((r) => r.data)
