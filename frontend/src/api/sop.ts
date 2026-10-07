/**
 * SOP library API — procedures drafted, approved and in force, and finding the
 * passage on something (backend/app/routers/sop.py).
 *
 * `ask` returns passages of approved procedures, word for word. It is not an
 * answer: nothing is composed.
 */
import { apiClient } from './client'

const BASE = '/api/v1/sop'

export type DocumentState = 'IN_FORCE' | 'NOT_YET_APPROVED' | 'EXPIRED' | 'RETIRED'
export type VersionState = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'

export interface Procedure {
  id: string
  code: string
  title: string
  category: string
  site_id: string | null
  site_name: string | null
  state: DocumentState
  is_retired: boolean
  in_force_version_id: string | null
  in_force_version_no: number | null
  effective_from: string | null
  effective_until: string | null
  version_count: number
  incident_types: string[]
  /** A version being written or awaiting a decision. Null for somebody who only reads. */
  open_version: { id: string; version_no: number; state: VersionState } | null
}

export interface Version {
  id: string
  document_id: string
  version_no: number
  body: string
  change_note: string | null
  state: VersionState
  drafted_by_name: string | null
  drafted_at: string
  submitted_at: string | null
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  effective_from: string | null
  effective_until: string | null
  attachment_name: string | null
  has_attachment: boolean
  in_force: boolean
  /** What this person may do with it. The server decides — and never lets its author approve it. */
  may: { edit: boolean; submit: boolean; withdraw: boolean; decide: boolean }
}

export interface ProcedureDetail extends Omit<Procedure, 'version_count'> {
  versions: Version[]
  can_write: boolean
  can_approve: boolean
}

export interface Library { items: Procedure[]; categories: string[]; can_write: boolean; can_approve: boolean }

export interface Passage {
  id: string
  heading: string | null
  /** The procedure's own words, as approved. */
  text: string
  matched_words: string[]
  matched: number
  of: number
  procedure: { id: string; code: string; title: string; category: string; site_name: string | null }
  version: { id: string; version_no: number; approved_at: string | null; approved_by_name: string | null
             effective_from: string | null; effective_until: string | null }
}

export interface Asked {
  question: string
  /** The words it was looked for by. */
  words: string[]
  passages: Passage[]
  note: string
  /** Always false: these are procedure text, not an answer. */
  is_an_answer: false
  nothing?: string
}

export interface RelevantProcedure {
  id: string
  code: string
  title: string
  category: string
  site_name: string | null
  for_types: string[]
  version: { id: string; version_no: number; approved_at: string | null; approved_by_name: string | null
             has_attachment: boolean }
  text: string
  passages: { heading: string | null; body: string }[]
}

export interface Relevant { incident_types: string[]; procedures: RelevantProcedure[]; why_none: string | null }

export const getLibrary = (params: { q?: string; category?: string; site_id?: string; state?: DocumentState | 'AWAITING' } = {}) =>
  apiClient.get<Library>(`${BASE}/documents`, { params }).then((r) => r.data)

export const getProcedure = (id: string) => apiClient.get<ProcedureDetail>(`${BASE}/documents/${id}`).then((r) => r.data)

export const writeProcedure = (body: { title: string; category: string; site_id: string | null; body: string
                                       incident_types: string[] }) =>
  apiClient.post<ProcedureDetail>(`${BASE}/documents`, body).then((r) => r.data)

export const setIncidentTypes = (id: string, incidentTypes: string[]) =>
  apiClient.put<ProcedureDetail>(`${BASE}/documents/${id}/incident-types`, { incident_types: incidentTypes })
    .then((r) => r.data)

export const draftNextVersion = (id: string) =>
  apiClient.post<ProcedureDetail>(`${BASE}/documents/${id}/versions`).then((r) => r.data)

export const retireProcedure = (id: string) => apiClient.post<ProcedureDetail>(`${BASE}/documents/${id}/retire`).then((r) => r.data)
export const restoreProcedure = (id: string) => apiClient.post<ProcedureDetail>(`${BASE}/documents/${id}/restore`).then((r) => r.data)

export const changeVersion = (id: string, body: { body?: string; change_note?: string | null }) =>
  apiClient.patch<Version>(`${BASE}/versions/${id}`, body).then((r) => r.data)

export const submitVersion = (id: string) => apiClient.post<Version>(`${BASE}/versions/${id}/submit`).then((r) => r.data)
export const withdrawVersion = (id: string) => apiClient.post<Version>(`${BASE}/versions/${id}/withdraw`).then((r) => r.data)

export const approveVersion = (id: string, body: { effective_from?: string | null; effective_until?: string | null
                                                   note?: string | null } = {}) =>
  apiClient.post<ProcedureDetail>(`${BASE}/versions/${id}/approve`, body).then((r) => r.data)

export const rejectVersion = (id: string, reason: string) =>
  apiClient.post<ProcedureDetail>(`${BASE}/versions/${id}/reject`, { reason }).then((r) => r.data)

export const attachDocument = (id: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiClient.post<{ attachment_name: string; attachment_sha256: string; bytes: number }>(
    `${BASE}/versions/${id}/attachment`, form).then((r) => r.data)
}

/** The document as issued, as a file the browser can open. */
export const downloadAttachment = (id: string) =>
  apiClient.get<Blob>(`${BASE}/versions/${id}/attachment`, { responseType: 'blob' }).then((r) => r.data)

export const askLibrary = (question: string, siteId?: string) =>
  apiClient.post<Asked>(`${BASE}/ask`, { question, site_id: siteId ?? null }).then((r) => r.data)

export const getIncidentTypes = () => apiClient.get<{ items: string[] }>(`${BASE}/incident-types`).then((r) => r.data.items)

export const getProceduresForIncident = (incidentId: string) =>
  apiClient.get<Relevant>(`${BASE}/for-incident/${incidentId}`).then((r) => r.data)

export const getProceduresForSituation = (situationId: string) =>
  apiClient.get<Relevant>(`${BASE}/for-situation/${situationId}`).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}
