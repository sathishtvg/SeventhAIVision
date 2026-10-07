/**
 * Visitor and contractor authorisation API — who said a visit may happen, for
 * where, for how long and with whom (backend/app/routers/visitor_authorizations.py).
 *
 * An authorisation informs the gate. It checks nobody in and refuses nobody:
 * registering and checking in a visitor are the existing endpoints', unchanged.
 */
import { apiClient } from './client'

const BASE = '/api/v1/visitor-authorizations'

export type AuthState = 'REQUESTED' | 'APPROVED' | 'DECLINED' | 'CANCELLED'
export type Standing = 'NOT_ASKED' | 'AWAITING_HOST' | 'LAPSED' | 'DECLINED' | 'CANCELLED' | 'NOT_YET_VALID' | 'VALID'
  | 'EXPIRED'
export type SubjectKind = 'visit' | 'work_permit'

export interface AuthPlace { id: string; name: string; kind: string; part_of: string | null; is_active: boolean }

export interface Authorisation {
  id: string
  site_id: string
  site_name: string
  purpose: string | null
  state: AuthState
  standing: Standing
  /** What stands on the record, in the sentences the gate reads. The server's words. */
  says: string[]
  valid_from: string
  valid_until: string
  host_user_id: string | null
  host_name: string | null
  escort_required: boolean
  escort_user_id: string | null
  escort_name: string | null
  escort_note: string | null
  /** The kind of document somebody saw. Its number is never taken. */
  id_document_kind: string | null
  id_checked_at: string | null
  id_checked_by_name: string | null
  requested_by_name: string | null
  requested_at: string
  decided_by_name: string | null
  decided_at: string | null
  decision_note: string | null
  extended_at: string | null
  extended_by_name: string | null
  extend_reason: string | null
  cancelled_at: string | null
  cancelled_by_name: string | null
  cancel_reason: string | null
  is_latest: boolean
  subject: { kind: SubjectKind; id: string; name: string | null; company: string | null; detail: string | null
             status: string | null; workers_count?: number }
  places: AuthPlace[]
  asked_of_me: boolean
  asked_by_me: boolean
  /** What this person may do with it. The server decides. */
  may: { approve: boolean; decline: boolean; cancel: boolean; extend: boolean; places: boolean; escort: boolean
         id_seen: boolean; review_movements: boolean }
}

export interface Movement {
  authorization_id: string
  access_event_id: string
  occurred_at: string
  event_type: string
  denial_reason: string | null
  door_name: string
  badge: string
  place_name: string | null
  part_of: string | null
  /** Null when it cannot be said; `why_not_known` then says why. */
  within: boolean | null
  in_period: boolean | null
  why_not_known: string | null
  /** Something for a person to look at. Not a finding. */
  to_look_at: boolean
  review: { outcome: 'IN_ORDER' | 'FOLLOWED_UP'; note: string | null; reviewed_at: string
            reviewed_by_name: string | null } | null
}

export interface Movements { available: boolean; why: string | null; badges: string[]; items: Movement[]; note: string }

export interface AuthorisationDetail extends Authorisation {
  /** Where the visitor's badge was used. Null for somebody who does not manage visits. */
  movements: Movements | null
  note: string
}

export interface Listed { items: Authorisation[]; limit: number; offset: number; has_more: boolean; can_ask: boolean
                          can_manage: boolean }

export interface ToReview {
  items: (Movement & { subject_name: string | null; site_name: string; valid_from: string; valid_until: string })[]
  days: number
  note: string
}

export interface Options {
  visits: { id: string; name: string; company: string | null; status: string; expected_from: string | null
            expected_until: string | null; host_user_id: string | null; host_name: string | null
            purpose: string | null; site_id: string | null }[]
  permits: { id: string; name: string; permit_number: string | null; work_description: string; status: string
             start_at: string; end_at: string; workers_count: number }[]
  places: { id: string; name: string; kind: string; part_of: string | null }[]
  people: { id: string; name: string }[]
  id_kinds: string[]
}

export interface AskBody {
  visitor_id?: string
  work_permit_id?: string
  site_id?: string
  host_user_id?: string | null
  purpose?: string | null
  valid_from?: string | null
  valid_until?: string | null
  escort_required?: boolean
  escort_user_id?: string | null
  escort_note?: string | null
  place_ids?: string[]
}

export const listAuthorisations = (params: { site_id?: string; standing?: Standing; subject?: SubjectKind; q?: string
                                             history?: boolean; limit?: number; offset?: number } = {}) =>
  apiClient.get<Listed>(BASE, { params }).then((r) => r.data)

export const getOptions = (siteId: string) =>
  apiClient.get<Options>(`${BASE}/options`, { params: { site_id: siteId } }).then((r) => r.data)

export const waitingForMe = () => apiClient.get<{ items: Authorisation[] }>(`${BASE}/mine`).then((r) => r.data.items)

export const movementsToReview = (days = 7) =>
  apiClient.get<ToReview>(`${BASE}/to-review`, { params: { days } }).then((r) => r.data)

export const getAuthorisation = (id: string) => apiClient.get<AuthorisationDetail>(`${BASE}/${id}`).then((r) => r.data)

export const askForAuthorisation = (body: AskBody) => apiClient.post<Authorisation>(BASE, body).then((r) => r.data)

export const approveAuthorisation = (id: string, note?: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/approve`, { note: note || null }).then((r) => r.data)

export const declineAuthorisation = (id: string, reason: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/decline`, { reason }).then((r) => r.data)

export const cancelAuthorisation = (id: string, reason: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/cancel`, { reason }).then((r) => r.data)

export const extendAuthorisation = (id: string, validUntil: string, reason: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/extend`, { valid_until: validUntil, reason }).then((r) => r.data)

export const setPlaces = (id: string, placeIds: string[]) =>
  apiClient.put<Authorisation>(`${BASE}/${id}/places`, { place_ids: placeIds }).then((r) => r.data)

export const setEscort = (id: string, body: { escort_required: boolean; escort_user_id?: string | null
                                              escort_note?: string | null }) =>
  apiClient.put<Authorisation>(`${BASE}/${id}/escort`, body).then((r) => r.data)

/** The kind of document that was seen. Not its number: the server refuses one. */
export const recordIdSeen = (id: string, kind: string) =>
  apiClient.post<Authorisation>(`${BASE}/${id}/id-seen`, { kind }).then((r) => r.data)

export const reviewMovement = (id: string, accessEventId: string, body: { outcome: 'IN_ORDER' | 'FOLLOWED_UP'
                                                                          note?: string | null }) =>
  apiClient.post<Movement>(`${BASE}/${id}/movements/${accessEventId}/review`, body).then((r) => r.data)

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}
