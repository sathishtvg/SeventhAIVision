import { apiClient } from './client'

/** Field types the visitor form builder supports; 'select'/'multiselect' are
 * the dropdown cases. Mirrors the CHECK in migration 0077. */
export type VisitorFieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'date'
  | 'select'
  | 'multiselect'
  | 'checkbox'
  | 'phone'
  | 'email'

export const FIELD_TYPE_LABELS: Record<VisitorFieldType, string> = {
  text: 'Text',
  textarea: 'Long text',
  number: 'Number',
  date: 'Date',
  select: 'Dropdown (one)',
  multiselect: 'Dropdown (many)',
  checkbox: 'Checkbox',
  phone: 'Phone',
  email: 'Email',
}

export const OPTION_FIELD_TYPES: VisitorFieldType[] = ['select', 'multiselect']

export interface VisitorFormField {
  id: string
  /** null = applies tenant-wide; otherwise specific to that site. */
  site_id: string | null
  field_key: string
  label: string
  field_type: VisitorFieldType
  options: string[]
  is_required: boolean
  placeholder: string | null
  help_text: string | null
  sort_order: number
  is_active: boolean
  created_at: string
}

export interface VisitorFormFieldInput {
  field_key: string
  label: string
  field_type: VisitorFieldType
  site_id?: string | null
  options?: string[]
  is_required?: boolean
  placeholder?: string | null
  help_text?: string | null
  sort_order?: number
}

export interface OnsiteVehicle {
  id: string
  full_name: string
  company: string | null
  vehicle_plate: string | null
  status: string
  /** vehicle | walk_in | delivery | drop_off | pick_up (migration 0087). */
  visit_type: VisitType
  purpose: string | null
  /** When the PERSON arrived. Always set for anyone on the board. */
  arrived_at: string
  /** When the VEHICLE entered — null for anyone on foot, which is why the
   *  parking clock and is_overstayed are vehicle-only. */
  vehicle_entry_at: string | null
  /** Full name of the user who registered this visit, for the gatehouse log. */
  registered_by: string | null
  site_id: string | null
  site_name: string | null
  custom_fields: Record<string, unknown>
  /** null = this site does not meter parking, so it can never overstay. */
  allowance_minutes: number | null
  minutes_on_site: number
  is_overstayed: boolean
}

export type VisitType = 'vehicle' | 'walk_in' | 'delivery' | 'drop_off' | 'pick_up'

/** Order matters: this is the order the operator sees them in, most common
 *  first, because a gatehouse picks one of these on every single entry. */
export const VISIT_TYPES: VisitType[] = ['walk_in', 'vehicle', 'delivery', 'drop_off', 'pick_up']

export const VISIT_TYPE_LABELS: Record<VisitType, string> = {
  walk_in: 'Walk-in',
  vehicle: 'Vehicle',
  delivery: 'Delivery',
  drop_off: 'Drop-off',
  pick_up: 'Pick-up',
}

export interface VisitorEntryPayload {
  full_name: string
  company?: string | null
  id_number?: string | null
  host_user_id?: string | null
  host_name?: string | null
  purpose?: string | null
  visitor_email?: string | null
  free_parking_minutes?: number | null
  custom_fields?: Record<string, unknown>
}

/** Realtime payload pushed when the entry LPR reads a plate that needs an
 * operator (see services/vms.py). Carries the form definition so the dialog
 * can render without a second round trip. */
export interface VisitorEntryPrompt {
  visitor_id: string
  site_id: string
  site_name: string
  camera_id: string
  plate_number: string
  display_name: string
  company: string | null
  owner_name: string | null
  category: string | null
  decision: string
  reason: string
  needs_details: boolean
  /** The plate read behind this prompt. Resolved to an image by the
   *  dialog via getEvidenceForDetection — see the note in vms.py. */
  detection_id: string | null
  free_parking_minutes: number | null
  form_fields: VisitorFormField[]
}

export const listFormFields = (siteId?: string) =>
  apiClient
    .get<VisitorFormField[]>('/api/v1/vms/form-fields', {
      params: siteId ? { site_id: siteId } : {},
    })
    .then((r) => r.data)

export const createFormField = (data: VisitorFormFieldInput) =>
  apiClient.post<VisitorFormField>('/api/v1/vms/form-fields', data).then((r) => r.data)

export const updateFormField = (id: string, data: Partial<VisitorFormFieldInput> & { is_active?: boolean }) =>
  apiClient.put<VisitorFormField>(`/api/v1/vms/form-fields/${id}`, data).then((r) => r.data)

export const deleteFormField = (id: string) =>
  apiClient.delete(`/api/v1/vms/form-fields/${id}`).then((r) => r.data)

/** Fill in the details for a visit the entry LPR already opened. */
export const completeVisitorEntry = (visitorId: string, data: VisitorEntryPayload) =>
  apiClient.post(`/api/v1/vms/entries/${visitorId}/complete`, data).then((r) => r.data)

/** Add a visitor by hand — the path for a site with no entry LPR configured. */
export const createManualEntry = (
  data: VisitorEntryPayload & { site_id: string; vehicle_plate?: string | null; visit_type: VisitType },
) => apiClient.post('/api/v1/vms/entries/manual', data).then((r) => r.data)

export const listOnsiteVehicles = (siteId?: string, overstayedOnly = false, visitType?: VisitType) =>
  apiClient
    .get<OnsiteVehicle[]>('/api/v1/vms/onsite', {
      params: {
        ...(siteId ? { site_id: siteId } : {}),
        ...(overstayedOnly ? { overstayed_only: true } : {}),
        ...(visitType ? { visit_type: visitType } : {}),
      },
    })
    .then((r) => r.data)
