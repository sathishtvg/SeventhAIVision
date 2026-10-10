import { apiClient } from './client'

export interface AlertsBySeverity {
  severity: string
  count: number
}

export interface AlertsByModule {
  module_type: string
  count: number
}

export interface DetectionTrendPoint {
  date: string
  count: number
}

export interface TopCamera {
  /** Null for the alerts that came from no camera (roster, payroll), counted together. */
  camera_id: string | null
  camera_name: string | null
  alert_count: number
}

export interface IncidentResolutionTime {
  period: string
  avg_hours: number
}

export const getAlertsBySeverity = (params?: { site_id?: string; days?: number }) =>
  apiClient
    .get<AlertsBySeverity[]>('/api/v1/analytics/alerts/by-severity', { params })
    .then((r) => r.data)

export const getAlertsByModule = (params?: { site_id?: string; days?: number }) =>
  apiClient
    .get<AlertsByModule[]>('/api/v1/analytics/alerts/by-module', { params })
    .then((r) => r.data)

export const getDetectionsTrend = (params?: { site_id?: string; days?: number }) =>
  apiClient
    .get<DetectionTrendPoint[]>('/api/v1/analytics/detections/trend', { params })
    .then((r) => r.data)

export const getTopCameras = (params?: { site_id?: string; days?: number; limit?: number }) =>
  apiClient
    .get<TopCamera[]>('/api/v1/analytics/top-cameras', { params })
    .then((r) => r.data)
