import { apiClient } from './client'

export interface AnalyticsSummary {
  open_alerts: number
  open_incidents: number
  active_cameras: number
  detections_today: number
  alerts_today: number
  alerts_7d: number
  detections_7d: number
}

export const getSummary = () =>
  apiClient.get<AnalyticsSummary>('/api/v1/analytics/summary').then((r) => r.data)
