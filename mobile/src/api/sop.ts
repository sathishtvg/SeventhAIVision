/**
 * The SOP library on the phone: the procedure for an incident, and finding the
 * passage on something (backend/app/routers/sop.py).
 *
 * What comes back is procedure text, exactly as it was approved. Nothing is
 * composed, and the phone presents none of it as an answer.
 */
import { apiClient } from './client'

const BASE = '/api/v1/sop'

export interface RelevantProcedure {
  id: string
  code: string
  title: string
  site_name: string | null
  version: { version_no: number; approved_at: string | null; approved_by_name: string | null }
  /** The whole procedure, as approved. */
  text: string
  passages: { heading: string | null; body: string }[]
}

export interface Relevant {
  incident_types: string[]
  procedures: RelevantProcedure[]
  /** Why nothing is put beside the incident, when nothing is. */
  why_none: string | null
}

export interface Passage {
  id: string
  heading: string | null
  text: string
  matched_words: string[]
  matched: number
  of: number
  procedure: { id: string; code: string; title: string }
  version: { version_no: number; approved_by_name: string | null }
}

export interface Asked {
  words: string[]
  passages: Passage[]
  note: string
  nothing?: string
}

/** The procedures in force for an incident of this kind, at its site. */
export const getProceduresForIncident = (incidentId: string) =>
  apiClient.get<Relevant>(`${BASE}/for-incident/${incidentId}`).then((r) => r.data)

/** The passages of approved procedures that use the words asked. */
export const askProcedures = (question: string) =>
  apiClient.post<Asked>(`${BASE}/ask`, { question }).then((r) => r.data)
