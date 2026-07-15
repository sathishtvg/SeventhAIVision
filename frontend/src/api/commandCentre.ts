import { apiClient } from './client'

export interface GuardStatus {
  guard_name: string
  shift_id: string
  site_id: string
  actual_start: string | null
  scheduled_end: string | null
  last_checkpoint_at: string | null
}

export interface SiteStatus {
  id: string
  name: string
  address: string | null
  cameras_online: number
  cameras_offline: number
  cameras_degraded: number
  cameras_total: number
  active_alerts: number
  critical_alerts: number
  high_alerts: number
  medium_alerts: number
  guards: GuardStatus[]
}

export interface RecentAlert {
  id: string
  title: string
  severity: string
  module_type: string
  status: string
  created_at: string | null
  camera_name: string
  site_name: string | null
}

export interface CCSummary {
  total_sites: number
  cameras_online: number
  cameras_offline: number
  cameras_degraded: number
  active_alerts: number
  critical_alerts: number
  high_alerts: number
  guards_on_duty: number
}

export interface CCOverview {
  summary: CCSummary
  sites: SiteStatus[]
  recent_alerts: RecentAlert[]
  guards: GuardStatus[]
}

export const getCCOverview = (): Promise<CCOverview> =>
  apiClient.get<CCOverview>('/api/v1/command-centre/overview').then((r) => r.data)
