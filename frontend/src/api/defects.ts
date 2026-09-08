import { apiClient } from './client'

export const DEFECT_CATEGORIES = [
  'lighting', 'plumbing', 'electrical', 'lift', 'door_access', 'cctv',
  'fire_safety', 'structural', 'cleanliness', 'landscaping', 'other',
] as const

export const DEFECT_SEVERITIES = ['low', 'medium', 'high', 'safety_hazard'] as const

export const DEFECT_STATUSES = [
  'open', 'reported', 'in_progress', 'resolved', 'closed',
] as const

export type DefectStatus = (typeof DEFECT_STATUSES)[number]
export type DefectSeverity = (typeof DEFECT_SEVERITIES)[number]

export interface Defect {
  id: string
  site_id: string
  site_name: string
  shift_id: string | null
  category: string
  location: string | null
  description: string
  severity: DefectSeverity
  status: DefectStatus
  reported_at: string
  reported_by_name: string | null
  has_photo: boolean
  referred_to: string | null
  referred_at: string | null
  reference_no: string | null
  resolved_at: string | null
  resolved_by_name: string | null
  resolution_notes: string | null
  days_open: number
  created_at: string
  updated_at: string
}

export interface DefectSummary {
  open: number
  reported: number
  in_progress: number
  resolved: number
  closed: number
  open_safety_hazards: number
  /** Still open after a fortnight — the number a supervisor gets asked about. */
  ageing: number
}

export const listDefects = (params: {
  site_id?: string
  status_filter?: string
  category?: string
  severity?: string
  active_only?: boolean
} = {}) => apiClient.get<Defect[]>('/api/v1/defects', { params }).then((r) => r.data)

export const getDefectSummary = (siteId?: string) =>
  apiClient
    .get<DefectSummary>('/api/v1/defects/summary', {
      params: siteId ? { site_id: siteId } : {},
    })
    .then((r) => r.data)

export const reportDefect = (data: {
  site_id: string
  description: string
  category?: string
  location?: string | null
  severity?: string
}) => apiClient.post<Defect>('/api/v1/defects', data).then((r) => r.data)

export const updateDefect = (
  id: string,
  data: Partial<{
    description: string
    category: string
    location: string | null
    severity: string
  }>,
) => apiClient.put<Defect>(`/api/v1/defects/${id}`, data).then((r) => r.data)

export const referDefect = (
  id: string,
  data: { referred_to: string; reference_no?: string | null; in_progress?: boolean },
) => apiClient.post(`/api/v1/defects/${id}/refer`, data).then((r) => r.data)

export const resolveDefect = (
  id: string,
  data: { resolution_notes?: string | null; close_without_fix?: boolean },
) => apiClient.post(`/api/v1/defects/${id}/resolve`, data).then((r) => r.data)

export const uploadDefectPhoto = (id: string, file: File) => {
  const form = new FormData()
  form.append('photo', file)
  return apiClient.post(`/api/v1/defects/${id}/photo`, form).then((r) => r.data)
}

/** Bearer-authenticated, so an <img src> cannot reach it — fetch the bytes and
 *  hand back an object URL the caller must revoke. */
export const fetchDefectPhoto = (id: string) =>
  apiClient
    .get(`/api/v1/defects/${id}/photo`, { responseType: 'blob' })
    .then((r) => URL.createObjectURL(r.data as Blob))
