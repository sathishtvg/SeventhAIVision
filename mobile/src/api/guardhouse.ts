/**
 * The guardhouse registers — keys, and lost & found.
 *
 * Both belong on mobile more than on the web, because both are filled in at
 * the counter while somebody is standing there waiting. A guard issuing a key
 * to a lift engineer is not going to walk to a desk first, which is why the
 * paper book survives at sites that already have a system.
 *
 * Shapes mirror frontend/src/api/guardhouse.ts.
 */
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
  is_overdue: boolean
  hours_out: number
}

export const listKeys = (siteId?: string) =>
  apiClient
    .get<SiteKey[]>('/api/v1/keys', { params: siteId ? { site_id: siteId } : undefined })
    .then((r) => r.data)

export const getOutstandingKeys = (siteId?: string) =>
  apiClient
    .get<{ out: number; overdue: number; keys: OutstandingKey[] }>('/api/v1/keys/outstanding', {
      params: siteId ? { site_id: siteId } : undefined,
    })
    .then((r) => r.data)

export const issueKey = (data: {
  key_id: string
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
  status: 'held' | 'claimed' | 'disposed' | 'handed_to_police'
  notes: string | null
  has_photo: boolean
  claimed_by_name: string | null
  claimed_id_type: string | null
  claimed_id_last4: string | null
  released_at: string | null
  disposal_method: string | null
  days_held: number
  due_for_disposal: boolean
  retention_days?: number
}

export const listLostFound = (params: {
  site_id?: string
  status_filter?: string
  search?: string
} = {}) => apiClient.get<LostFoundItem[]>('/api/v1/lost-found', { params }).then((r) => r.data)

export const logLostFoundItem = (data: {
  description: string
  category?: string
  site_id?: string | null
  found_location?: string | null
  storage_location?: string | null
  notes?: string | null
}) => apiClient.post<LostFoundItem>('/api/v1/lost-found', data).then((r) => r.data)

export const releaseLostFoundItem = (
  id: string,
  data: {
    claimed_by_name: string
    claimed_by_contact?: string | null
    claimed_id_type?: string | null
    claimed_id_last4?: string | null
  },
) => apiClient.post(`/api/v1/lost-found/${id}/release`, data).then((r) => r.data)

/** The photo is captured at the counter — that is what settles a disputed
 *  claim later, and it cannot be reconstructed after the item has gone. */
export const uploadLostFoundPhoto = (id: string, uri: string) => {
  const form = new FormData()
  const name = uri.split('/').pop() || 'item.jpg'
  const type = name.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg'
  // React Native's FormData takes this shape for a file; the DOM typings do not
  // describe it, hence the cast.
  form.append('photo', { uri, name, type } as unknown as Blob)
  return apiClient.post(`/api/v1/lost-found/${id}/photo`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data)
}

// ── Facility defects ─────────────────────────────────────────────────────────
//
// The one register where mobile is not a convenience: a guard finds the blown
// light while standing in the stairwell, and a photo taken there is worth more
// than a description typed at a desk an hour later.

export const DEFECT_CATEGORIES = [
  'lighting', 'plumbing', 'electrical', 'lift', 'door_access', 'cctv',
  'fire_safety', 'structural', 'cleanliness', 'landscaping', 'other',
] as const

export const DEFECT_SEVERITIES = ['low', 'medium', 'high', 'safety_hazard'] as const

export interface Defect {
  id: string
  site_id: string
  site_name: string
  category: string
  location: string | null
  description: string
  severity: string
  status: string
  reported_at: string
  reported_by_name: string | null
  has_photo: boolean
  referred_to: string | null
  reference_no: string | null
  resolved_at: string | null
  resolution_notes: string | null
  days_open: number
}

export const listDefects = (params: {
  site_id?: string
  active_only?: boolean
} = {}) => apiClient.get<Defect[]>('/api/v1/defects', { params }).then((r) => r.data)

export const reportDefect = (data: {
  site_id: string
  description: string
  category?: string
  location?: string | null
  severity?: string
}) => apiClient.post<Defect>('/api/v1/defects', data).then((r) => r.data)

export const uploadDefectPhoto = (id: string, uri: string) => {
  const form = new FormData()
  const name = uri.split('/').pop() || 'defect.jpg'
  const type = name.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg'
  form.append('photo', { uri, name, type } as unknown as Blob)
  return apiClient.post(`/api/v1/defects/${id}/photo`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data)
}

// ── What this officer is holding ─────────────────────────────────────────────

export interface HeldByUser {
  user: { id: string; full_name: string | null; email: string; employee_code: string | null }
  equipment: {
    id: string
    item_id: string
    asset_code: string
    name: string
    category: string
    serial_number: string | null
    issued_at: string
    expected_return_at: string | null
    purpose: string | null
    is_overdue: boolean
  }[]
  uniform: {
    id: string
    item_type: string
    size: string | null
    quantity: number
    returned_quantity: number
    outstanding_quantity: number
    issued_at: string
    deposit_amount: number | null
    notes: string | null
  }[]
  summary: {
    equipment_out: number
    equipment_overdue: number
    uniform_pieces_out: number
    deposit_held: number
  }
}

export const getHeldByUser = (userId: string) =>
  apiClient.get<HeldByUser>(`/api/v1/equipment/assigned/${userId}`).then((r) => r.data)
