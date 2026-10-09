/**
 * The platform owner's switch for Drone Patrol, one organisation at a time
 * (backend/app/routers/platform_drone_licenses.py).
 *
 * Drone Patrol has a licence of its own rather than a row in the product
 * catalogue: it carries limits — how many drones, missions and sites — and an
 * expiry. A limit left empty is no limit.
 */
import { apiClient } from './client'

export interface DroneLicense {
  tenant_id: string
  tenant_name: string
  tenant_slug: string
  /** Whether the organisation may use Drone Patrol now: switched on, and not expired. */
  licensed: boolean
  /** Why not, in the server's words, when it may not. */
  reason: string | null
  is_enabled: boolean
  licensed_at: string | null
  expires_at: string | null
  max_drones: number | null
  max_missions: number | null
  max_sites: number | null
  notes: string | null
  updated_at: string | null
}

export interface DroneLicenseBody {
  is_enabled: boolean
  expires_at: string | null
  max_drones: number | null
  max_missions: number | null
  max_sites: number | null
  notes: string | null
}

export const getDroneLicense = (tenantId: string) =>
  apiClient.get<DroneLicense>(`/api/v1/platform/tenants/${tenantId}/drone-license`).then((r) => r.data)

export const putDroneLicense = (tenantId: string, body: DroneLicenseBody) =>
  apiClient.put<DroneLicense>(`/api/v1/platform/tenants/${tenantId}/drone-license`, body).then((r) => r.data)

/** The server's reason, in its own words. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => (x.msg ?? String(x)).replace(/^Value error, /, '')).join('; ')
  return e?.message ?? 'Something went wrong.'
}
