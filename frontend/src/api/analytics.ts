import type {
  AnalyticsSummary, AnalyticsCount, AnalyticsTrendPoint, TopCamera, IncidentResolutionTime, HeatmapCamera,
} from '@/types/api'
import { apiClient } from './client'

export const getSummary = (siteId?: string) =>
  apiClient
    .get<AnalyticsSummary>('/api/v1/analytics/summary', { params: siteId ? { site_id: siteId } : undefined })
    .then((r) => r.data)

export const getAlertsBySeverity = (days = 30) =>
  apiClient
    .get<Array<{ severity: string; count: number }>>('/api/v1/analytics/alerts/by-severity', { params: { days } })
    .then((r) => r.data.map((d) => ({ label: d.severity, count: d.count }) as AnalyticsCount))

export const getAlertsByModule = (days = 30) =>
  apiClient
    .get<Array<{ module_type: string; count: number }>>('/api/v1/analytics/alerts/by-module', { params: { days } })
    .then((r) => r.data.map((d) => ({ label: d.module_type, count: d.count }) as AnalyticsCount))

export const getDetectionsTrend = (days = 7) =>
  apiClient
    .get<AnalyticsTrendPoint[]>('/api/v1/analytics/detections/trend', { params: { days } })
    .then((r) => r.data)

export const getAlertsTrend = (days = 7) =>
  apiClient
    .get<AnalyticsTrendPoint[]>('/api/v1/analytics/alerts/trend', { params: { days } })
    .then((r) => r.data)

export const getTopCameras = (days = 30, limit = 10) =>
  apiClient
    .get<TopCamera[]>('/api/v1/analytics/top-cameras', { params: { days, limit } })
    .then((r) => r.data)

export const getIncidentResolutionTime = (days = 30) =>
  apiClient
    .get<IncidentResolutionTime>('/api/v1/analytics/incidents/resolution-time', { params: { days } })
    .then((r) => r.data)

export const getHeatmapData = (hours = 24, moduleType?: string, siteId?: string) =>
  apiClient
    .get<HeatmapCamera[]>('/api/v1/analytics/heatmap', {
      params: { hours, ...(moduleType ? { module_type: moduleType } : {}), ...(siteId ? { site_id: siteId } : {}) },
    })
    .then((r) => r.data)
