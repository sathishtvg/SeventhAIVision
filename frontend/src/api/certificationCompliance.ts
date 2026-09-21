import { apiClient } from './client'

const BASE = '/api/v1/certification-compliance'

/**
 * Worst first. The order matters on screen: a guard with no licence at all is a
 * different conversation from one whose renewal is due in three weeks.
 */
export type FindingStatus = 'MISSING' | 'REVOKED' | 'EXPIRED' | 'EXPIRING'

export interface CertificationRequirement {
  id: string
  /** null means the tenant baseline — every site, no exceptions. */
  site_id: string | null
  site_name: string | null
  certification_type: string
  warn_days_before: number
  is_active: boolean
  notes: string | null
  created_at: string
}

export interface CertificationFinding {
  id: string
  shift_id: string
  certification_type: string
  status: FindingStatus
  shift_date: string
  detected_at: string
  resolved_at: string | null
  guard_name: string
  guard_email: string
  site_name: string | null
  scheduled_start: string | null
}

export interface ComplianceSummary {
  /**
   * Returned so the page can tell "nothing is wrong" apart from "nobody has
   * said what to check". Zero findings against zero requirements is not a
   * clean bill of health.
   */
  requirements_configured: number
  by_status: Partial<Record<FindingStatus, number>>
  open_total: number
  horizon_days: number
}

export const listRequirements = (params?: { site_id?: string }) =>
  apiClient
    .get<CertificationRequirement[]>(`${BASE}/requirements`, { params })
    .then((r) => r.data)

export const addRequirement = (body: {
  certification_type: string
  site_id?: string | null
  warn_days_before?: number
  notes?: string | null
}) =>
  apiClient
    .post<CertificationRequirement>(`${BASE}/requirements`, body)
    .then((r) => r.data)

export const deleteRequirement = (id: string) =>
  apiClient.delete(`${BASE}/requirements/${id}`).then((r) => r.data)

export const listFindings = (params?: {
  site_id?: string
  days?: number
  include_resolved?: boolean
}) =>
  apiClient
    .get<CertificationFinding[]>(`${BASE}/findings`, { params })
    .then((r) => r.data)

export const getSummary = (params?: { days?: number }) =>
  apiClient.get<ComplianceSummary>(`${BASE}/summary`, { params }).then((r) => r.data)
