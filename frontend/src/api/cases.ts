/**
 * Security cases (backend/app/routers/cases.py).
 *
 * A case refers to what it is about — an incident, an investigation, an
 * evidence package — and copies none of it. A linked record is read under its
 * own permission: one the reader may not read comes back as a kind and a
 * state, with no label and no id.
 *
 * What the reader may do to a case is the server's to say (`may`). Closing
 * takes two people; a closed case is not changed.
 */
import { apiClient } from './client'

const BASE = '/api/v1/cases'

export type Status = 'OPEN' | 'AWAITING_APPROVAL' | 'CLOSED'
export type Category = 'THEFT' | 'TRESPASS' | 'DAMAGE' | 'SAFETY' | 'ACCESS' | 'OTHER'
export type Priority = 'LOW' | 'NORMAL' | 'HIGH'
export type LinkKind = 'INCIDENT' | 'INVESTIGATION' | 'EVIDENCE_PACKAGE'
export type LinkState = 'SHOWN' | 'NOT_PERMITTED' | 'NOT_AVAILABLE'
export type PartyKind = 'PERSON' | 'VEHICLE'
export type Connection = 'REPORTED_IT' | 'WITNESS' | 'AFFECTED' | 'NAMED' | 'OTHER'

export interface CaseRow {
  id: string
  case_number: string
  title: string
  category: Category
  category_label: string
  priority: Priority
  status: Status
  status_label: string
  site_id: string | null
  site_name: string | null
  lead_name: string | null
  opened_at: string
  closed_at: string | null
  tasks_open: number
  links: number
}

export interface CaseList { items: CaseRow[]; total: number; can_open: boolean; can_manage: boolean }

export interface Task {
  id: string
  title: string
  detail: string | null
  assigned_to_user_id: string | null
  assigned_to_name: string | null
  due_at: string | null
  state: 'OPEN' | 'DONE' | 'DROPPED'
  created_by_name: string | null
  done_by_name: string | null
  done_note: string | null
  dropped_reason: string | null
  /** Somebody given a task may finish it, whether or not they are on the case. */
  may_finish: boolean
}

export interface Link {
  id: string
  kind: LinkKind
  kind_label: string
  state: LinkState
  /** The permission the record is read under. */
  needs: string
  /** Null unless the reader may read the record. */
  ref_id: string | null
  label: string | null
  detail: string | null
  note: string | null
  linked_at: string
  linked_by_name: string | null
}

export interface Party {
  id: string
  kind: PartyKind
  label: string
  connection: Connection
  connection_label: string
  note: string | null
  added_by_name: string | null
}

export interface Entry { id: string; kind: string; words: string; body: string | null; occurred_at: string; actor_name: string | null }

export interface CaseFile {
  id: string
  case_number: string
  title: string
  summary: string
  category: Category
  category_label: string
  priority: Priority
  status: Status
  status_label: string
  site: { id: string; name: string } | null
  lead_user_id: string | null
  lead_name: string | null
  opened_at: string
  opened_by_name: string | null
  outcome: string | null
  close_requested_at: string | null
  close_requested_by_name: string | null
  closed_at: string | null
  closed_by_name: string | null
  investigators: { user_id: string; name: string | null }[]
  tasks: Task[]
  tasks_open: number
  links: Link[]
  parties: Party[]
  entries: Entry[]
  on_case: boolean
  may: { work: boolean; assign: boolean; request_close: boolean; approve_close: boolean; decline_close: boolean; reopen: boolean }
  party_note: string
  two_people_note: string
}

export interface Recent { id: string; label: string; detail: string | null }

export interface CaseOptions {
  categories: { key: Category; label: string }[]
  priorities: Priority[]
  connections: { key: Connection; label: string }[]
  link_kinds: { key: LinkKind; label: string; needs: string; may: boolean }[]
  people: { id: string; name: string | null }[]
  recent: Record<LinkKind, Recent[]>
  party_note: string
}

export const listCases = (params: { status?: Status; site_id?: string; mine?: boolean } = {}) =>
  apiClient.get<CaseList>(BASE, { params }).then((r) => r.data)

export const getCaseOptions = () => apiClient.get<CaseOptions>(`${BASE}/options`).then((r) => r.data)

export const getCase = (id: string) => apiClient.get<CaseFile>(`${BASE}/${id}`).then((r) => r.data)

export const openCase = (body: {
  title: string; summary: string; site_id?: string | null; category: Category; priority: Priority
  from_kind?: LinkKind; from_id?: string
}) => apiClient.post<CaseFile>(BASE, body).then((r) => r.data)

const act = (id: string, path: string, body?: unknown, method: 'post' | 'put' | 'patch' = 'post') =>
  apiClient[method]<CaseFile>(`${BASE}/${id}${path}`, body).then((r) => r.data)

export const changeCase = (id: string, body: { title?: string; summary?: string; category?: Category; priority?: Priority }) =>
  act(id, '', body, 'patch')
export const setLead = (id: string, userId: string) => act(id, '/lead', { user_id: userId }, 'put')
export const addInvestigator = (id: string, userId: string) => act(id, '/investigators', { user_id: userId })
export const removeInvestigator = (id: string, userId: string) => act(id, `/investigators/${userId}/remove`)
export const addNote = (id: string, body: string) => act(id, '/notes', { body })
export const addTask = (id: string, body: { title: string; assigned_to_user_id?: string | null }) => act(id, '/tasks', body)
export const finishTask = (id: string, taskId: string, how: 'done' | 'drop', note: string | null) =>
  act(id, `/tasks/${taskId}/${how}`, { note })
export const addLink = (id: string, body: { kind: LinkKind; ref_id: string; note?: string | null }) => act(id, '/links', body)
export const removeLink = (id: string, linkId: string, reason: string) => act(id, `/links/${linkId}/remove`, { reason })
export const addParty = (id: string, body: { kind: PartyKind; label: string; connection: Connection; note?: string | null }) =>
  act(id, '/parties', body)
export const removeParty = (id: string, partyId: string, reason: string) => act(id, `/parties/${partyId}/remove`, { reason })
export const requestClose = (id: string, outcome: string) => act(id, '/request-close', { outcome })
export const approveClose = (id: string) => act(id, '/approve-close')
export const declineClose = (id: string, reason: string) => act(id, '/decline-close', { reason })
export const reopenCase = (id: string, reason: string) => act(id, '/reopen', { reason })

/** The case as a PDF, handed to the browser to save. Taking it is written in the audit log. */
export async function downloadReport(id: string, number: string): Promise<void> {
  const response = await apiClient.get(`${BASE}/${id}/report.pdf`, { responseType: 'blob' })
  const href = URL.createObjectURL(response.data as Blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `${number}.pdf`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(href)
}

/** The server's reason, in its own words: a refusal here says what to do instead. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}
