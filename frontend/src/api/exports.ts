import { apiClient } from './client'

type AlertExportParams = {
  date_from?: string
  date_to?: string
  severity?: string
  status?: string
  module_type?: string
}

type IncidentExportParams = {
  date_from?: string
  date_to?: string
  severity?: string
  status?: string
}

type DetectionExportParams = {
  date_from?: string
  date_to?: string
  module_type?: string
  camera_id?: string
}

type AuditExportParams = {
  date_from?: string
  date_to?: string
  action?: string
  resource_type?: string
}

function _clean(params: Record<string, string | undefined>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  ) as Record<string, string>
}

async function _download(url: string, params: Record<string, string | undefined>, defaultFilename: string) {
  const response = await apiClient.get(url, {
    params: _clean(params),
    responseType: 'blob',
  })

  const contentDisposition = response.headers['content-disposition'] as string | undefined
  const match = contentDisposition?.match(/filename="([^"]+)"/)
  const filename = match?.[1] ?? defaultFilename

  const href = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(href)
}

export const exportAlerts = (params: AlertExportParams = {}) =>
  _download('/api/v1/export/alerts', params, 'alerts.csv')

export const exportIncidents = (params: IncidentExportParams = {}) =>
  _download('/api/v1/export/incidents', params, 'incidents.csv')

export const exportDetections = (params: DetectionExportParams = {}) =>
  _download('/api/v1/export/detections', params, 'detections.csv')

export const exportAuditLogs = (params: AuditExportParams = {}) =>
  _download('/api/v1/export/audit-logs', params, 'audit_logs.csv')
