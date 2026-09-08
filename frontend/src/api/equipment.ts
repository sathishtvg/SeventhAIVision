import { apiClient } from './client'

export const EQUIPMENT_CATEGORIES = [
  'radio', 'torch', 'baton', 'handcuffs', 'bodycam', 'ppe',
  'phone', 'vehicle', 'metal_detector', 'first_aid', 'other',
] as const

export const EQUIPMENT_CONDITIONS = [
  'new', 'good', 'fair', 'poor', 'damaged', 'lost',
] as const

export const UNIFORM_TYPES = [
  'shirt', 'trousers', 'jacket', 'beret', 'cap', 'belt',
  'shoes', 'epaulette', 'name_tag', 'tie', 'raincoat', 'other',
] as const

export interface EquipmentItem {
  id: string
  site_id: string | null
  site_name: string | null
  asset_code: string
  name: string
  category: string
  serial_number: string | null
  condition: string
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  // Present only while it is out.
  open_assignment_id: string | null
  issued_at: string | null
  expected_return_at: string | null
  purpose: string | null
  assigned_to_user_id: string | null
  held_by_name: string | null
  held_by_employee_code: string | null
  issued_by_name: string | null
  is_out: boolean
  is_overdue: boolean
}

export interface EquipmentHistoryRow {
  id: string
  issued_at: string
  expected_return_at: string | null
  returned_at: string | null
  condition_on_return: string | null
  return_notes: string | null
  purpose: string | null
  held_by_name: string | null
  issued_by_name: string | null
  received_by_name: string | null
}

export interface OutstandingEquipment {
  id: string
  item_id: string
  asset_code: string
  name: string
  category: string
  site_id: string | null
  site_name: string | null
  issued_at: string
  expected_return_at: string | null
  purpose: string | null
  assigned_to_user_id: string
  held_by_name: string | null
  held_by_employee_code: string | null
  issued_by_name: string | null
  is_overdue: boolean
  hours_out: number
}

export interface UniformIssue {
  id: string
  user_id: string
  full_name: string | null
  employee_code: string | null
  item_type: string
  size: string | null
  quantity: number
  returned_quantity: number
  outstanding_quantity: number
  issued_at: string
  returned_at: string | null
  deposit_amount: number | null
  notes: string | null
  issued_by_name: string | null
}

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

export const listEquipment = (params: {
  site_id?: string
  category?: string
  out_only?: boolean
  include_inactive?: boolean
} = {}) => apiClient.get<EquipmentItem[]>('/api/v1/equipment', { params }).then((r) => r.data)

export const getOutstandingEquipment = (siteId?: string) =>
  apiClient
    .get<{ out: number; overdue: number; items: OutstandingEquipment[] }>(
      '/api/v1/equipment/outstanding',
      { params: siteId ? { site_id: siteId } : {} },
    )
    .then((r) => r.data)

export const getEquipmentItem = (id: string) =>
  apiClient
    .get<EquipmentItem & { history: EquipmentHistoryRow[] }>(`/api/v1/equipment/${id}`)
    .then((r) => r.data)

export const getHeldByUser = (userId: string) =>
  apiClient.get<HeldByUser>(`/api/v1/equipment/assigned/${userId}`).then((r) => r.data)

export const createEquipment = (data: {
  asset_code: string
  name: string
  category?: string
  site_id?: string | null
  serial_number?: string | null
  condition?: string
  notes?: string | null
}) => apiClient.post<EquipmentItem>('/api/v1/equipment', data).then((r) => r.data)

export const updateEquipment = (
  id: string,
  data: Partial<{
    asset_code: string
    name: string
    category: string
    site_id: string | null
    serial_number: string | null
    condition: string
    notes: string | null
    is_active: boolean
  }>,
) => apiClient.put<EquipmentItem>(`/api/v1/equipment/${id}`, data).then((r) => r.data)

export const issueEquipment = (data: {
  item_id: string
  assigned_to_user_id: string
  expected_return_at?: string | null
  purpose?: string | null
}) => apiClient.post('/api/v1/equipment/issue', data).then((r) => r.data)

export const receiveEquipment = (
  assignmentId: string,
  data: { condition_on_return?: string | null; return_notes?: string | null },
) => apiClient
  .post(`/api/v1/equipment/assignments/${assignmentId}/receive`, data)
  .then((r) => r.data)

export const listUniformIssues = (params: {
  user_id?: string
  outstanding_only?: boolean
} = {}) => apiClient
  .get<UniformIssue[]>('/api/v1/equipment/uniforms', { params })
  .then((r) => r.data)

export const issueUniform = (data: {
  user_id: string
  item_type: string
  size?: string | null
  quantity?: number
  deposit_amount?: string | null
  notes?: string | null
}) => apiClient.post<UniformIssue>('/api/v1/equipment/uniforms', data).then((r) => r.data)

/** Absolute, not incremental — "three of the four are back" is what somebody
 *  counting a pile can state truthfully. */
export const returnUniform = (
  issueId: string,
  data: { returned_quantity: number; notes?: string | null },
) => apiClient
  .post(`/api/v1/equipment/uniforms/${issueId}/return`, data)
  .then((r) => r.data)
