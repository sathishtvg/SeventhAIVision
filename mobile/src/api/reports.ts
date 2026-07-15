import { apiClient } from './client'

export const getReportUrl = (
  type: 'site-summary' | 'dob' | 'incident',
  params: Record<string, string>,
) => {
  const baseUrl = (apiClient.defaults.baseURL ?? '').replace(/\/$/, '')
  const query = new URLSearchParams(params).toString()
  return `${baseUrl}/api/v1/reports/${type}?${query}`
}

export const getSiteSummaryReport = (params: {
  site_id: string
  report_type?: 'daily' | 'weekly'
  date?: string
}) =>
  apiClient
    .get('/api/v1/reports/site-summary', {
      params,
      responseType: 'blob',
    })
    .then((r) => r.data as Blob)

export const getDOBReport = (params: {
  date_from?: string
  date_to?: string
  site_id?: string
}) =>
  apiClient
    .get('/api/v1/reports/dob', { params, responseType: 'blob' })
    .then((r) => r.data as Blob)

export const getIncidentReport = (incidentId: string) =>
  apiClient
    .get(`/api/v1/reports/incident/${incidentId}`, { responseType: 'blob' })
    .then((r) => r.data as Blob)
