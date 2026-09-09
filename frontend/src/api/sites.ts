import type { Site } from '@/types/api'
import { apiClient } from './client'

export const getSites = (isActive?: boolean) =>
  apiClient
    .get<Site[]>('/api/v1/sites', { params: isActive !== undefined ? { is_active: isActive } : {} })
    .then((r) => r.data)

export const getSite = (siteId: string) =>
  apiClient.get<Site>(`/api/v1/sites/${siteId}`).then((r) => r.data)

export const createSite = (data: {
  name: string
  address?: string
  description?: string
  latitude?: number
  longitude?: number
  geofence_radius_meters?: number
  late_grace_minutes?: number
  /** Drawn boundary; overrides geofence_radius_meters when set. */
  geofence_polygon?: { lat: number; lng: number }[] | null
  client_id?: string
  bill_rate?: number
  /** Officers this site is staffed for, per shift type. 0 is valid — plenty
   *  of sites run no night shift at all. */
  day_guards_required?: number
  night_guards_required?: number
}) => apiClient.post<Site>('/api/v1/sites', data).then((r) => r.data)

export const updateSite = (
  siteId: string,
  data: {
    name?: string
    address?: string
    description?: string
    latitude?: number
    longitude?: number
    geofence_radius_meters?: number
  late_grace_minutes?: number
  /** Drawn boundary; overrides geofence_radius_meters when set. */
  geofence_polygon?: { lat: number; lng: number }[] | null
    is_active?: boolean
    client_id?: string
    bill_rate?: number
    day_guards_required?: number
    night_guards_required?: number
    // VMS config is update-only: the cameras have to exist and be assigned to
    // the site before they can be bound to its entry/exit lanes.
    //
    // These three are `| null` rather than optional-undefined on purpose —
    // the backend keys off which fields are PRESENT in the request, so sending
    // an explicit null is the only way to UNBIND a camera or clear the parking
    // allowance. Omitting them leaves the stored value untouched.
    vms_enabled?: boolean
    entry_lpr_camera_id?: string | null
    exit_lpr_camera_id?: string | null
    free_parking_minutes?: number | null
  }
) => apiClient.put(`/api/v1/sites/${siteId}`, data).then((r) => r.data)

export const deactivateSite = (siteId: string) =>
  apiClient.delete(`/api/v1/sites/${siteId}`).then((r) => r.data)

export const getSiteCameras = (siteId: string) =>
  apiClient.get(`/api/v1/sites/${siteId}/cameras`).then((r) => r.data)

export const validateStream = (data: { url: string; username?: string; password?: string }) =>
  apiClient.post('/api/v1/cameras/validate-stream', data).then((r) => r.data)

// ── Duty assignments ────────────────────────────────────────────────────────
//
// Who stands at a site, on which shift. Separate from user_sites, which says
// which sites a person may SEE — a supervisor can be scoped to twelve sites
// without being on any of their teams.

export interface DutyAssignment {
  id: string
  guard_user_id: string
  shift_type: 'day' | 'night'
  notes: string | null
  created_at: string
  full_name: string | null
  email: string
  phone: string | null
  designation: string | null
  employment_type: string | null
  /** The guard's own stated preference, so the page can show where a posting
   *  disagrees with it. Not an error — worth seeing, though. */
  preferred_shift_type: 'day' | 'night' | null
  /** Why this person may appear on more than one site's team: supervisors and
   *  managers cover several sites, and standby officers relieve across them.
   *  Everyone else stands exactly one post — enforced in the database by
   *  migration 0101. */
  role_id: number
  is_standby: boolean
}

export interface DutyOverviewRow {
  site_id: string
  site_name: string
  day_guards_required: number
  night_guards_required: number
  day_assigned: number
  night_assigned: number
}

export const getDutyOverview = () =>
  apiClient.get<DutyOverviewRow[]>('/api/v1/sites/duty-assignments/overview').then((r) => r.data)

/** Every duty posting tenant-wide — who stands where. The team dialog uses it
 *  to avoid offering an officer who already holds a post at another site. */
export interface DutyPosting {
  guard_user_id: string
  site_id: string
  site_name: string
  shift_type: 'day' | 'night'
  role_id: number
  is_standby: boolean
}

export const getDutyPostings = () =>
  apiClient.get<DutyPosting[]>('/api/v1/sites/duty-assignments/postings').then((r) => r.data)

export const getDutyAssignments = (siteId: string) =>
  apiClient.get<DutyAssignment[]>(`/api/v1/sites/${siteId}/duty-assignments`).then((r) => r.data)

export const addDutyAssignment = (
  siteId: string,
  data: { guard_user_id: string; shift_type: 'day' | 'night'; notes?: string },
) => apiClient.post(`/api/v1/sites/${siteId}/duty-assignments`, data).then((r) => r.data)

export const removeDutyAssignment = (siteId: string, assignmentId: string) =>
  apiClient.delete(`/api/v1/sites/${siteId}/duty-assignments/${assignmentId}`).then((r) => r.data)
