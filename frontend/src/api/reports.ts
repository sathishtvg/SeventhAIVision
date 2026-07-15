import { apiClient } from './client'

export const downloadSiteSummaryReport = async (
  siteId: string,
  dateFrom: string,
  dateUntil: string
): Promise<void> => {
  const response = await apiClient.get('/api/v1/reports/site-summary', {
    params: { site_id: siteId, date_from: dateFrom, date_until: dateUntil },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `site_summary_${dateFrom}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}

export const downloadDobReport = async (
  dateFrom: string,
  dateUntil: string,
  siteId?: string
): Promise<void> => {
  const response = await apiClient.get('/api/v1/reports/dob', {
    params: { date_from: dateFrom, date_until: dateUntil, site_id: siteId },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `daily_occurrence_book_${dateFrom}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}

export const downloadIncidentReport = async (incidentId: string): Promise<void> => {
  const response = await apiClient.get(`/api/v1/reports/incident/${incidentId}`, {
    responseType: 'blob',
  })
  const url = URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `incident_${incidentId.slice(0, 8)}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}

export const getPdpaConsents = (params?: { site_id?: string; consent_type?: string }) =>
  apiClient.get('/api/v1/pdpa/consents', { params }).then((r) => r.data)

export const getDsars = (params?: { dsar_status?: string }) =>
  apiClient.get('/api/v1/pdpa/dsar', { params }).then((r) => r.data)

export const createDsar = (data: {
  request_type: string
  data_subject_name: string
  data_subject_id?: string
  data_subject_email?: string
  description?: string
}) => apiClient.post('/api/v1/pdpa/dsar', data).then((r) => r.data)

export const updateDsar = (dsarId: string, data: { status: string; fulfillment_notes?: string; records_erased?: number }) =>
  apiClient.put(`/api/v1/pdpa/dsar/${dsarId}`, data).then((r) => r.data)
