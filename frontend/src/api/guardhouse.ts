import { apiClient } from './client'

// ── Keys ─────────────────────────────────────────────────────────────────────

export interface SiteKey {
  id: string
  site_id: string
  site_name?: string
  key_code: string
  label: string
  cabinet_position: string | null
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  // Present only while the key is out — a LEFT JOIN on the open transaction.
  open_transaction_id: string | null
  issued_at: string | null
  expected_return_at: string | null
  purpose: string | null
  held_by_name: string | null
  held_by_company: string | null
  held_by_contact: string | null
  issued_by_name: string | null
  is_out: boolean
  is_overdue: boolean
}

export interface OutstandingKey {
  id: string
  key_id: string
  key_code: string
  label: string
  cabinet_position: string | null
  site_id: string
  site_name: string
  issued_at: string
  expected_return_at: string | null
  purpose: string | null
  held_by_name: string | null
  held_by_company: string | null
  held_by_contact: string | null
  issued_by_name: string | null
  is_overdue: boolean
  hours_out: number
}

export interface KeyTransaction {
  id: string
  key_id: string
  key_code: string
  label: string
  site_id: string
  site_name: string
  issued_at: string
  expected_return_at: string | null
  purpose: string | null
  returned_at: string | null
  return_notes: string | null
  held_by_name: string | null
  held_by_company: string | null
  issued_by_name: string | null
  received_by_name: string | null
}

export const listKeys = (params: { site_id?: string; out_only?: boolean; include_inactive?: boolean } = {}) =>
  apiClient.get<SiteKey[]>('/api/v1/keys', { params }).then((r) => r.data)

export const getOutstandingKeys = (siteId?: string) =>
  apiClient
    .get<{ out: number; overdue: number; keys: OutstandingKey[] }>('/api/v1/keys/outstanding', {
      params: siteId ? { site_id: siteId } : {},
    })
    .then((r) => r.data)

export const listKeyTransactions = (params: { key_id?: string; site_id?: string; limit?: number } = {}) =>
  apiClient.get<KeyTransaction[]>('/api/v1/keys/transactions', { params }).then((r) => r.data)

export const createKey = (data: {
  site_id: string
  key_code: string
  label: string
  cabinet_position?: string | null
  notes?: string | null
}) => apiClient.post<SiteKey>('/api/v1/keys', data).then((r) => r.data)

export const updateKey = (
  id: string,
  data: Partial<{
    key_code: string
    label: string
    cabinet_position: string | null
    notes: string | null
    is_active: boolean
  }>,
) => apiClient.put<SiteKey>(`/api/v1/keys/${id}`, data).then((r) => r.data)

export const issueKey = (data: {
  key_id: string
  issued_to_user_id?: string | null
  issued_to_name?: string | null
  issued_to_company?: string | null
  issued_to_contact?: string | null
  expected_return_at?: string | null
  purpose?: string | null
}) => apiClient.post('/api/v1/keys/issue', data).then((r) => r.data)

export const returnKey = (transactionId: string, returnNotes?: string) =>
  apiClient
    .post(`/api/v1/keys/transactions/${transactionId}/return`, {
      return_notes: returnNotes || null,
    })
    .then((r) => r.data)

// ── Lost & found ─────────────────────────────────────────────────────────────

export const LOST_FOUND_CATEGORIES = [
  'wallet', 'phone', 'keys', 'bag', 'clothing',
  'jewellery', 'documents', 'electronics', 'other',
] as const

export type LostFoundStatus = 'held' | 'claimed' | 'disposed' | 'handed_to_police'

export interface LostFoundItem {
  id: string
  site_id: string | null
  site_name: string | null
  description: string
  category: string
  found_location: string | null
  found_at: string
  found_by_name: string | null
  storage_location: string | null
  status: LostFoundStatus
  notes: string | null
  has_photo: boolean
  claimed_by_name: string | null
  claimed_by_contact: string | null
  claimed_id_type: string | null
  claimed_id_last4: string | null
  released_by_name: string | null
  released_at: string | null
  disposed_at: string | null
  disposal_method: string | null
  days_held: number
  due_for_disposal: boolean
  retention_days?: number
  created_at: string
  updated_at: string
}

export interface LostFoundSummary {
  held: number
  claimed: number
  disposed: number
  handed_to_police: number
  due_for_disposal: number
  retention_days: number
}

export const listLostFound = (params: {
  site_id?: string
  status_filter?: string
  category?: string
  search?: string
  overdue_only?: boolean
} = {}) => apiClient.get<LostFoundItem[]>('/api/v1/lost-found', { params }).then((r) => r.data)

export const getLostFoundSummary = (siteId?: string) =>
  apiClient
    .get<LostFoundSummary>('/api/v1/lost-found/summary', {
      params: siteId ? { site_id: siteId } : {},
    })
    .then((r) => r.data)

export const logLostFoundItem = (data: {
  description: string
  category?: string
  site_id?: string | null
  found_location?: string | null
  found_at?: string | null
  storage_location?: string | null
  notes?: string | null
}) => apiClient.post<LostFoundItem>('/api/v1/lost-found', data).then((r) => r.data)

export const updateLostFoundItem = (
  id: string,
  data: Partial<{
    description: string
    category: string
    found_location: string | null
    storage_location: string | null
    notes: string | null
  }>,
) => apiClient.put<LostFoundItem>(`/api/v1/lost-found/${id}`, data).then((r) => r.data)

export const releaseLostFoundItem = (
  id: string,
  data: {
    claimed_by_name: string
    claimed_by_contact?: string | null
    claimed_id_type?: string | null
    claimed_id_last4?: string | null
    notes?: string | null
  },
) => apiClient.post(`/api/v1/lost-found/${id}/release`, data).then((r) => r.data)

export const disposeLostFoundItem = (
  id: string,
  data: { disposal_method: string; handed_to_police?: boolean; notes?: string | null },
) => apiClient.post(`/api/v1/lost-found/${id}/dispose`, data).then((r) => r.data)

export const uploadLostFoundPhoto = (id: string, file: File) => {
  const form = new FormData()
  form.append('photo', file)
  return apiClient.post(`/api/v1/lost-found/${id}/photo`, form).then((r) => r.data)
}

/** The photo endpoint is bearer-authenticated, so an <img src> cannot reach
 *  it directly — fetch the bytes and hand back an object URL the caller must
 *  revoke. */
export const fetchLostFoundPhoto = (id: string) =>
  apiClient
    .get(`/api/v1/lost-found/${id}/photo`, { responseType: 'blob' })
    .then((r) => URL.createObjectURL(r.data as Blob))
