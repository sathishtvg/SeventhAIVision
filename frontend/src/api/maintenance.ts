/**
 * Maintenance API — work orders, the schedules that put them forward, and
 * whether device health may (backend/app/routers/maintenance.py).
 *
 * The platform suggests; a person raises the work. A `SUGGESTED` order is a row
 * in a list until somebody who manages maintenance accepts or dismisses it.
 */
import { apiClient } from './client'

const BASE = '/api/v1/maintenance'

export type OrderState = 'SUGGESTED' | 'OPEN' | 'IN_PROGRESS' | 'DONE' | 'CANCELLED' | 'DISMISSED'
export type OrderKind = 'CORRECTIVE' | 'PREVENTIVE' | 'INSPECTION'
export type Priority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT'
export type Origin = 'PERSON' | 'DEFECT' | 'HEALTH' | 'SCHEDULE'

export interface WorkOrder {
  id: string
  number: string
  site_id: string | null
  site_name: string | null
  asset_id: string | null
  asset_code: string | null
  asset_name: string | null
  schedule_id: string | null
  defect_id: string | null
  title: string
  description: string | null
  kind: OrderKind
  priority: Priority
  state: OrderState
  origin: Origin
  /** The fact it was put forward from, as it was then. The server's words. */
  suggestion_reason: string | null
  raised_by_name: string | null
  raised_at: string
  accepted_by_name: string | null
  accepted_at: string | null
  assigned_to_user_id: string | null
  assigned_to_user_name: string | null
  /** A vendor or technician who is not one of the organisation's people. */
  assigned_to_name: string | null
  due_at: string | null
  started_at: string | null
  started_by_name: string | null
  completed_at: string | null
  completed_by_name: string | null
  completion_note: string | null
  parts_used: string | null
  downtime_minutes: number | null
  closed_at: string | null
  closed_by_name: string | null
  closed_reason: string | null
  overdue: boolean
  assigned_to_me: boolean
  /** That it is only a suggestion. Null once it is work. */
  note: string | null
  /** What this person may do with it. The server decides. */
  may: { accept: boolean; dismiss: boolean; change: boolean; start: boolean; complete: boolean; cancel: boolean }
}

export interface Orders {
  items: WorkOrder[]
  limit: number
  offset: number
  has_more: boolean
  counts: { suggested: number; open: number; in_progress: number; overdue: number }
  can_manage: boolean
  states: OrderState[]
  kinds: OrderKind[]
  priorities: Priority[]
}

export interface Schedule {
  id: string
  site_id: string | null
  site_name: string | null
  asset_id: string | null
  asset_code: string | null
  asset_name: string | null
  title: string
  instructions: string | null
  every_days: number
  lead_days: number
  next_due_on: string
  last_done_on: string | null
  is_active: boolean
  days_until_due: number
}

export interface MaintenanceOptions {
  assets: { id: string; asset_code: string; name: string; kind: string; site_id: string | null; site_name: string | null }[]
  defects: { id: string; category: string; location: string | null; description: string; severity: string
             site_id: string; site_name: string | null }[]
  people: { id: string; name: string }[]
  kinds: OrderKind[]
  priorities: Priority[]
}

export interface MaintenanceSettings { suggest_from_health: boolean; suggest_after_hours: number
                                       default_after_hours: number; can_manage: boolean; note: string }

export interface RaiseBody {
  title: string
  description?: string | null
  kind?: OrderKind
  priority?: Priority
  site_id?: string | null
  asset_id?: string | null
  defect_id?: string | null
  due_at?: string | null
  assigned_to_user_id?: string | null
  assigned_to_name?: string | null
}

export const listOrders = (params: { state?: OrderState; site_id?: string; kind?: OrderKind; origin?: Origin
                                     mine?: boolean; overdue?: boolean; q?: string } = {}) =>
  apiClient.get<Orders>(`${BASE}/work-orders`, { params }).then((r) => r.data)

export const getOrder = (id: string) => apiClient.get<WorkOrder>(`${BASE}/work-orders/${id}`).then((r) => r.data)

export const getMaintenanceOptions = (siteId?: string) =>
  apiClient.get<MaintenanceOptions>(`${BASE}/options`, { params: siteId ? { site_id: siteId } : {} }).then((r) => r.data)

export const raiseOrder = (body: RaiseBody) => apiClient.post<WorkOrder>(`${BASE}/work-orders`, body).then((r) => r.data)

/** What is left out stays; what is given as null is cleared. */
export const changeOrder = (id: string, body: Partial<Pick<RaiseBody, 'title' | 'description' | 'priority' | 'due_at'
                                                            | 'asset_id' | 'assigned_to_user_id' | 'assigned_to_name'>>) =>
  apiClient.patch<WorkOrder>(`${BASE}/work-orders/${id}`, body).then((r) => r.data)

/** A suggestion becomes work, and may be given to somebody in the same step. */
export const acceptOrder = (id: string, body: { assigned_to_user_id?: string | null; assigned_to_name?: string | null
                                                due_at?: string | null; priority?: Priority } = {}) =>
  apiClient.post<WorkOrder>(`${BASE}/work-orders/${id}/accept`, body).then((r) => r.data)

export const dismissOrder = (id: string, reason: string) =>
  apiClient.post<WorkOrder>(`${BASE}/work-orders/${id}/dismiss`, { reason }).then((r) => r.data)

export const startOrder = (id: string) => apiClient.post<WorkOrder>(`${BASE}/work-orders/${id}/start`).then((r) => r.data)

export const completeOrder = (id: string, body: { completion_note: string; parts_used?: string | null
                                                  downtime_minutes?: number | null }) =>
  apiClient.post<WorkOrder>(`${BASE}/work-orders/${id}/complete`, body).then((r) => r.data)

export const cancelOrder = (id: string, reason: string) =>
  apiClient.post<WorkOrder>(`${BASE}/work-orders/${id}/cancel`, { reason }).then((r) => r.data)

export const listSchedules = () =>
  apiClient.get<{ items: Schedule[]; can_manage: boolean }>(`${BASE}/schedules`).then((r) => r.data)

export const addSchedule = (body: { title: string; instructions?: string | null; site_id?: string | null
                                    asset_id?: string | null; every_days: number; lead_days?: number
                                    next_due_on: string }) =>
  apiClient.post<Schedule>(`${BASE}/schedules`, body).then((r) => r.data)

export const changeSchedule = (id: string, body: Partial<{ title: string; instructions: string | null
                                                           every_days: number; lead_days: number
                                                           next_due_on: string; is_active: boolean }>) =>
  apiClient.patch<Schedule>(`${BASE}/schedules/${id}`, body).then((r) => r.data)

export const getMaintenanceSettings = () => apiClient.get<MaintenanceSettings>(`${BASE}/settings`).then((r) => r.data)

export const setMaintenanceSettings = (body: { suggest_from_health: boolean; suggest_after_hours: number }) =>
  apiClient.put<{ suggest_from_health: boolean; suggest_after_hours: number; changed: boolean }>(`${BASE}/settings`, body)
    .then((r) => r.data)

export { apiError } from './securityAssets'
