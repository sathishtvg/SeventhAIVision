/**
 * The fleet API (`/api/v1/fleet`): vehicles, their custom fields and groups.
 *
 * A vehicle is archived, never deleted: archiving takes it off lists and the
 * map, keeps its history and can be undone. Every change is in the audit log.
 * What a person may do is said by the server (`may`); the server checks it again.
 */
import { apiClient } from './client'

export type ServiceStatus = 'in_service' | 'maintenance' | 'out_of_service'
export type FieldType = 'text' | 'number' | 'date' | 'select' | 'boolean'
export type CustomValue = string | number | boolean

export interface FleetMay { edit: boolean; archive: boolean; manage_fields: boolean }

export interface FleetVehicle {
  id: string
  name: string
  plate_number: string | null
  plate_key?: string | null
  vehicle_type: string
  type_label: string
  make: string | null
  model: string | null
  color: string | null
  site_id: string | null
  site_name: string | null
  department: string | null
  service_status: ServiceStatus
  service_label: string
  assigned_driver_user_id: string | null
  driver_name: string | null
  driver_phone?: string | null
  notes?: string | null
  custom_fields?: Record<string, CustomValue>
  current_status: string
  last_position_at: string | null
  archived: boolean
  archived_at: string | null
  archive_reason: string | null
  created_at: string
  updated_at: string
}

export interface VehicleField {
  id: string
  field_key: string
  label: string
  field_type: FieldType
  options: string[]
  is_required: boolean
  help_text: string | null
  sort_order: number
  is_active: boolean
}

export interface FleetVehicleDetail extends FleetVehicle {
  driver_display: string | null
  driver_user_name: string | null
  archived_by_name: string | null
  groups: { id: string; name: string; site_id: string | null }[]
  fields: VehicleField[]
  may: FleetMay
  archived_note: string
}

export interface VehicleList { items: FleetVehicle[]; total: number; limit: number; offset: number; may: FleetMay }

export interface FleetOptions {
  vehicle_types: { value: string; label: string }[]
  service_statuses: { value: ServiceStatus; label: string }[]
  field_types: FieldType[]
  sites: { id: string; name: string }[]
  drivers: { id: string; full_name: string }[]
  fields: VehicleField[]
  site_required: boolean
  may: FleetMay
}

export interface VehicleHistoryLine { action: string; created_at: string; detail: Record<string, unknown> | null; by_name: string | null }

export interface FleetGroup {
  id: string
  name: string
  description: string | null
  site_id: string | null
  site_name: string | null
  is_active: boolean
  vehicles: number
  created_at: string
}

export interface VehicleInput {
  name?: string
  plate_number?: string | null
  vehicle_type?: string
  make?: string | null
  model?: string | null
  color?: string | null
  site_id?: string | null
  department?: string | null
  assigned_driver_user_id?: string | null
  driver_name?: string | null
  driver_phone?: string | null
  service_status?: ServiceStatus
  notes?: string | null
  custom_fields?: Record<string, CustomValue | null>
}

export interface VehicleQuery {
  q?: string
  site_id?: string
  vehicle_type?: string
  service_status?: string
  group_id?: string
  archived?: 'active' | 'archived' | 'all'
  sort?: string
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

const BASE = '/api/v1/fleet'

export const listFleetVehicles = (params: VehicleQuery): Promise<VehicleList> =>
  apiClient.get(`${BASE}/vehicles`, { params }).then((r) => r.data)
export const getFleetVehicle = (id: string): Promise<FleetVehicleDetail> =>
  apiClient.get(`${BASE}/vehicles/${id}`).then((r) => r.data)
export const createFleetVehicle = (body: VehicleInput): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles`, body).then((r) => r.data)
export const changeFleetVehicle = (id: string, body: VehicleInput): Promise<FleetVehicle> =>
  apiClient.patch(`${BASE}/vehicles/${id}`, body).then((r) => r.data)
export const archiveFleetVehicle = (id: string, reason: string): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles/${id}/archive`, { reason }).then((r) => r.data)
export const restoreFleetVehicle = (id: string): Promise<FleetVehicle> =>
  apiClient.post(`${BASE}/vehicles/${id}/restore`).then((r) => r.data)
export const getVehicleHistory = (id: string): Promise<{ items: VehicleHistoryLine[] }> =>
  apiClient.get(`${BASE}/vehicles/${id}/history`).then((r) => r.data)
export const getFleetOptions = (): Promise<FleetOptions> =>
  apiClient.get(`${BASE}/options`).then((r) => r.data)

export const listVehicleFields = (): Promise<{ items: VehicleField[] }> =>
  apiClient.get(`${BASE}/vehicle-fields`).then((r) => r.data)
export const createVehicleField = (body: Partial<VehicleField>): Promise<VehicleField> =>
  apiClient.post(`${BASE}/vehicle-fields`, body).then((r) => r.data)
export const changeVehicleField = (id: string, body: Partial<VehicleField>): Promise<VehicleField> =>
  apiClient.patch(`${BASE}/vehicle-fields/${id}`, body).then((r) => r.data)
export const setVehicleFieldInUse = (id: string, inUse: boolean): Promise<VehicleField> =>
  apiClient.post(`${BASE}/vehicle-fields/${id}/${inUse ? 'reinstate' : 'retire'}`).then((r) => r.data)

export const listFleetGroups = (includeRetired = false): Promise<{ items: FleetGroup[] }> =>
  apiClient.get(`${BASE}/groups`, { params: includeRetired ? { include_retired: true } : undefined }).then((r) => r.data)
export const createFleetGroup = (body: { name: string; description?: string | null; site_id?: string | null }): Promise<FleetGroup> =>
  apiClient.post(`${BASE}/groups`, body).then((r) => r.data)
export const changeFleetGroup = (id: string, body: { name?: string; description?: string | null; site_id?: string | null }): Promise<FleetGroup> =>
  apiClient.patch(`${BASE}/groups/${id}`, body).then((r) => r.data)
export const retireFleetGroup = (id: string): Promise<FleetGroup> =>
  apiClient.post(`${BASE}/groups/${id}/retire`).then((r) => r.data)
export const getGroupVehicles = (id: string): Promise<{ items: { id: string; name: string; plate_number: string | null; site_id: string | null; archived_at: string | null }[] }> =>
  apiClient.get(`${BASE}/groups/${id}/vehicles`).then((r) => r.data)
export const setGroupVehicles = (id: string, vehicleIds: string[]): Promise<{ added: number; removed: number; vehicles: number }> =>
  apiClient.put(`${BASE}/groups/${id}/vehicles`, { vehicle_ids: vehicleIds }).then((r) => r.data)

/** How a vehicle is named to a person: its plate first, because that is what is on the vehicle. */
export function vehicleLabel(v: { plate_number: string | null; name: string }): string {
  return v.plate_number ? `${v.plate_number} — ${v.name}` : v.name
}

/** The server's reason, in its own words. */
export function fleetError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  return e?.message ?? 'Something went wrong.'
}

/** The audit log's action names, in words. */
export const HISTORY_WORDS: Record<string, string> = {
  'fleet.vehicle.created': 'Registered',
  'fleet.vehicle.changed': 'Changed',
  'fleet.vehicle.archived': 'Archived',
  'fleet.vehicle.restored': 'Restored',
}

/** "Fuel card no." becomes `fuel_card_no`: the key a new field is offered, which the person may change before saving. */
export function fieldKeyOf(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^[^a-z]+/, '').slice(0, 50)
}
