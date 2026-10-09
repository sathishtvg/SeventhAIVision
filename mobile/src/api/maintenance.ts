/**
 * Maintenance work given to this person (backend/app/routers/maintenance.py).
 *
 * The phone shows the work orders assigned to whoever is signed in, and lets
 * them say that they have started and what they did. Raising, accepting,
 * assigning and cancelling are done at a desk. What this person may do with an
 * order is the server's to say (`may`).
 */
import { apiClient } from './client'

const BASE = '/api/v1/maintenance/work-orders'

export type OrderState = 'SUGGESTED' | 'OPEN' | 'IN_PROGRESS' | 'DONE' | 'CANCELLED' | 'DISMISSED'

export interface WorkOrder {
  id: string
  number: string
  site_name: string | null
  asset_code: string | null
  asset_name: string | null
  title: string
  description: string | null
  kind: 'CORRECTIVE' | 'PREVENTIVE' | 'INSPECTION'
  priority: 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT'
  state: OrderState
  due_at: string | null
  started_at: string | null
  overdue: boolean
  assigned_to_me: boolean
  may: { start: boolean; complete: boolean }
}

/** The orders given to whoever is signed in that are not over. */
export const listMyOrders = () =>
  apiClient.get<{ items: WorkOrder[] }>(BASE, { params: { mine: true } }).then((r) => r.data.items)

export const startOrder = (id: string) => apiClient.post<WorkOrder>(`${BASE}/${id}/start`).then((r) => r.data)

/** What was done is said in words; parts and time out of use only when there is something to say. */
export function completeOrder(id: string, note: string, parts?: string | null, downtimeMinutes?: number | null) {
  const body: Record<string, unknown> = { completion_note: note.trim() }
  if (parts && parts.trim()) body.parts_used = parts.trim()
  if (downtimeMinutes != null) body.downtime_minutes = downtimeMinutes
  return apiClient.post<WorkOrder>(`${BASE}/${id}/complete`, body).then((r) => r.data)
}
