/**
 * Visitor authorisation on the phone: what stands for a visit at the gate, the
 * visits waiting for the caller's answer, and asking the host
 * (backend/app/routers/visitor_authorizations.py).
 *
 * It informs. Checking a visitor in is ./visitors, as it always was, and
 * nothing here stops it.
 */
import { apiClient } from './client'

const BASE = '/api/v1/visitor-authorizations'

export type Standing = 'NOT_ASKED' | 'AWAITING_HOST' | 'LAPSED' | 'DECLINED' | 'CANCELLED' | 'NOT_YET_VALID' | 'VALID'
  | 'EXPIRED'

export interface Authorisation {
  id: string
  site_name: string
  purpose: string | null
  standing: Standing
  /** What stands on the record, in the server's own sentences. */
  says: string[]
  valid_from: string
  valid_until: string
  host_name: string | null
  id_document_kind: string | null
  subject: { kind: 'visit' | 'work_permit'; id: string; name: string | null; company: string | null
             detail: string | null }
  asked_of_me: boolean
  /** What this person may do with it. The server decides. */
  may: { approve: boolean; decline: boolean; cancel: boolean; id_seen: boolean }
}

export interface Stands {
  standing: Standing
  says: string[]
  authorization: Authorisation | null
  /** That this informs, and checks nobody in or out. */
  note: string
}

/** The authorisations waiting for the caller's answer, whoever has waited longest first. */
export const getWaitingForMe = () =>
  apiClient.get<{ items: Authorisation[] }>(`${BASE}/mine`).then((r) => r.data.items)

/** What stands on the record for one visit. */
export const getStanding = (visitorId: string) =>
  apiClient.get<Stands>(`${BASE}/standing`, { params: { visitor_id: visitorId } }).then((r) => r.data)

/** Ask for a visit to be authorised. The host and the period are the visit's own. */
export const askForVisit = (visitorId: string) =>
  apiClient.post<Authorisation>(BASE, { visitor_id: visitorId }).then((r) => r.data)

export const approve = (id: string) => apiClient.post<Authorisation>(`${BASE}/${id}/approve`, {}).then((r) => r.data)

/** Say no, and why. */
export const decline = (id: string, reason: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/decline`, { reason }).then((r) => r.data)

/** The kind of document that was seen. Its number is not sent: the server would refuse it. */
export const recordIdSeen = (id: string, kind: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/id-seen`, { kind }).then((r) => r.data)
