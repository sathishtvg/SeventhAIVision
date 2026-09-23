import { apiClient } from './client'

/**
 * A door, as the server describes it.
 *
 * THERE IS NO LOCK STATE, and the app used to claim there was. `is_locked` and
 * `held_open` were never columns and never returned, so every door rendered as
 * unlocked — a guard reading this screen was told every door on the site was
 * open, always. access_doors holds what is below and nothing more; live state
 * lives in the events the readers report.
 */
export interface Door {
  id: string
  name: string
  location: string | null
  door_type: string
  site_id: string | null
  site_name: string | null
  camera_id: string | null
  camera_name: string | null
  is_active: boolean
  created_at: string
  updated_at: string | null
}

export interface AccessCredential {
  id: string
  holder_name: string | null
  user_id: string | null
  credential_type: string
  credential_ref: string
  is_active: boolean
  expires_at: string | null
  created_at: string
}

export interface AccessEvent {
  id: string
  door_id: string
  door_name: string | null
  credential_id: string | null
  holder_name: string | null
  event_type: string
  method: string | null
  occurred_at: string
}

export const getDoors = (params?: { site_id?: string }) =>
  apiClient.get<Door[]>('/api/v1/access/doors', { params }).then((r) => r.data)

export const getCredentials = (params?: { limit?: number }) =>
  apiClient
    .get<AccessCredential[]>('/api/v1/access/credentials', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getAccessEvents = (params?: { door_id?: string; event_type?: string; limit?: number }) =>
  apiClient
    .get<AccessEvent[]>('/api/v1/access/events', { params: { limit: 50, ...params } })
    .then((r) => r.data)

/*
 * NO lockDoor / unlockDoor HERE, DELIBERATELY.
 *
 * They used to POST to /access/doors/{id}/lock and /unlock. Neither route has
 * ever existed, and nothing behind the API could have carried them out: the
 * door table has no lock state, the only door write is event INGESTION for a
 * reader reporting what happened (granted, denied, forced, held_open, tamper —
 * all observations), and the web app has no such control either. This platform
 * does not command door hardware.
 *
 * Re-adding a button here means adding real hardware integration first. A
 * control that reports success while nothing moves is worse than no control: a
 * guard taps Lock, sees it confirmed, and walks away from an open door.
 */
