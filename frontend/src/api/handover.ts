import { apiClient } from './client'

/** What a counted checklist item can be measured against. Mirrors the CHECK
 *  constraint in migration 0099. */
export const COUNT_SOURCES = ['keys_out', 'lost_found_held', 'equipment_out'] as const

export const COUNT_SOURCE_LABELS: Record<string, string> = {
  keys_out: 'Keys out of the cabinet',
  lost_found_held: 'Property held',
  equipment_out: 'Equipment signed out',
}

export type HandoverStatus = 'submitted' | 'accepted' | 'disputed' | 'resolved'

export interface ChecklistItem {
  id: string
  template_id: string
  label: string
  requires_count: boolean
  expected_source: string | null
  is_required: boolean
  sort_order: number
}

export interface ChecklistTemplate {
  id: string
  site_id: string | null
  site_name: string | null
  name: string
  is_active: boolean
  items: ChecklistItem[]
  created_at?: string
  updated_at?: string
}

export interface HandoverCheck {
  id: string
  item_id: string | null
  label: string
  requires_count: boolean
  is_required: boolean
  sort_order: number
  checked: boolean
  counted_value: number | null
  /** What the system believed when the handover was raised. */
  expected_value: number | null
  notes: string | null
  mismatch: boolean
}

export interface Handover {
  id: string
  shift_id: string
  status: HandoverStatus
  created_at: string
  site_id: string | null
  site_name: string | null
  scheduled_start: string | null
  scheduled_end: string | null
  open_incidents_count: number
  open_alerts_count: number
  patrol_routes_completed: number
  patrol_routes_total: number
  checkpoints_scanned: number
  checkpoints_total: number
  keys_outstanding: number
  keys_overdue: number
  lost_found_held: number
  open_defects_count: number
  equipment_out_count: number
  outgoing_notes: string | null
  incoming_notes: string | null
  dispute_reason: string | null
  accepted_at: string | null
  resolved_at: string | null
  outgoing_guard_name: string | null
  incoming_guard_name: string | null
  accepted_by_name: string | null
  resolved_by_name: string | null
  incoming_guard_id: string | null
}

export interface HandoverDetail extends Handover {
  checks: HandoverCheck[]
  mismatches: number
  unchecked_required: number
}

export const listHandovers = (params: {
  site_id?: string
  status_filter?: string
  open_only?: boolean
} = {}) => apiClient.get<Handover[]>('/api/v1/handovers', { params }).then((r) => r.data)

export const getHandover = (id: string) =>
  apiClient.get<HandoverDetail>(`/api/v1/handovers/${id}`).then((r) => r.data)

export const updateChecks = (
  id: string,
  checks: { id: string; checked: boolean; counted_value?: number | null; notes?: string | null }[],
) => apiClient
  .put<{ updated: number; checks: HandoverCheck[]; mismatches: number; unchecked_required: number }>(
    `/api/v1/handovers/${id}/checks`, { checks },
  )
  .then((r) => r.data)

export const acceptHandover = (id: string, incomingNotes?: string | null) =>
  apiClient
    .post(`/api/v1/handovers/${id}/accept`, { incoming_notes: incomingNotes || null })
    .then((r) => r.data)

export const disputeHandover = (id: string, reason: string) =>
  apiClient
    .post(`/api/v1/handovers/${id}/dispute`, { dispute_reason: reason })
    .then((r) => r.data)

export const resolveHandover = (id: string, notes?: string | null) =>
  apiClient
    .post(`/api/v1/handovers/${id}/resolve`, { resolution_notes: notes || null })
    .then((r) => r.data)

// ── Checklists ───────────────────────────────────────────────────────────────

export const listTemplates = (includeInactive = false) =>
  apiClient
    .get<ChecklistTemplate[]>('/api/v1/handovers/templates', {
      params: { include_inactive: includeInactive },
    })
    .then((r) => r.data)

export const createTemplate = (data: { name: string; site_id?: string | null }) =>
  apiClient.post<ChecklistTemplate>('/api/v1/handovers/templates', data).then((r) => r.data)

export const updateTemplate = (
  id: string,
  data: Partial<{ name: string; is_active: boolean }>,
) => apiClient.put<ChecklistTemplate>(`/api/v1/handovers/templates/${id}`, data).then((r) => r.data)

export const addChecklistItem = (
  templateId: string,
  data: {
    label: string
    requires_count?: boolean
    expected_source?: string | null
    is_required?: boolean
    sort_order?: number
  },
) => apiClient
  .post<ChecklistItem>(`/api/v1/handovers/templates/${templateId}/items`, data)
  .then((r) => r.data)

export const updateChecklistItem = (
  id: string,
  data: Partial<{
    label: string
    requires_count: boolean
    expected_source: string | null
    is_required: boolean
    sort_order: number
  }>,
) => apiClient.put<ChecklistItem>(`/api/v1/handovers/items/${id}`, data).then((r) => r.data)

export const deleteChecklistItem = (id: string) =>
  apiClient.delete(`/api/v1/handovers/items/${id}`).then((r) => r.data)
