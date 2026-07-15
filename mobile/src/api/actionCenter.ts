import { apiClient } from './client'

export type ActionCategory =
  | 'contact_guard' | 'ack_alert' | 'overdue_checkpoint' | 'pending_approval' | 'camera_offline'
  | 'geofence_flag'
  | 'check_in' | 'patrol_due' | 'respond_incident' | 'doc_expiry'

export interface ActionItem {
  id: string
  category: ActionCategory
  severity: 'critical' | 'high' | 'medium' | 'low'
  title: string
  subtitle: string
  action_route?: string | null
  phone?: string | null
  entity_id: string
}

export interface ActionCenterResponse {
  summary: { total: number; critical: number; high: number; medium: number; low: number }
  items: ActionItem[]
}

// Self-scoped server-side by role (action_center.py) — a guard token only
// ever gets its own duty items (check_in/patrol_due/respond_incident/doc_expiry).
export const getMyDuties = () =>
  apiClient.get<ActionCenterResponse>('/api/v1/action-center').then((r) => r.data)
