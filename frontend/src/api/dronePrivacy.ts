/**
 * Privacy areas for drones (4 November 2026; Phase 5 of the known limits) — typed to
 * backend/app/routers/drone_privacy.py. Listing needs drone:read; drawing and taking away need privacy:manage.
 */
import { apiClient } from './client'
import type { ZoneShape } from './drones'

const BASE = '/api/v1/drone-privacy-areas'

export interface PrivacyArea {
  id: string
  site_id: string
  site_name: string
  name: string
  shape: ZoneShape
  polygon: { lat: number; lng: number }[] | null
  center_latitude: number | null
  center_longitude: number | null
  radius_m: number | null
  /** How far around the area the drone's picture is withheld too. */
  margin_m: number
  created_at: string
  created_by_name: string | null
}

export interface PrivacyAreaList {
  areas: PrivacyArea[]
  margin_default_m: number
  margin_most_m: number
  applies_within_seconds: number
  note: string
}

export interface PrivacyAreaInput {
  site_id: string
  name: string
  shape: ZoneShape
  polygon: { lat: number; lng: number }[] | null
  center_latitude: number | null
  center_longitude: number | null
  radius_m: number | null
  margin_m: number
}

/** A privacy area a route crosses: how far over it, and about how long the picture is blank there. */
export interface PrivacyCrossing { area_id: string; name: string; metres: number; seconds: number }

export const listPrivacyAreas = () => apiClient.get<PrivacyAreaList>(BASE).then((r) => r.data)

export const drawPrivacyArea = (body: PrivacyAreaInput) =>
  apiClient.post<PrivacyArea>(BASE, body).then((r) => r.data)

export const removePrivacyArea = (id: string) =>
  apiClient.post<{ removed: boolean }>(`${BASE}/${id}/remove`).then((r) => r.data)
