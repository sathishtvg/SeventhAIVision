/**
 * Staff faces for check-in (docs/plans/2026-10-23-staff-face-registration-design.md).
 *
 * A person registers their own face once, on the phone. On the web a supervisor
 * changes a guard's face and an admin a supervisor's, from an uploaded photo with
 * the person's agreement; and reviews the check-ins whose selfie did not match.
 */
import { apiClient } from './client'

export type FaceSource = 'SELFIE' | 'UPLOAD'
export type FaceOutcome = 'MATCHED' | 'MISMATCH' | 'NO_FACE' | 'NOT_CHECKED'

export interface StaffFace {
  user_id: string
  full_name: string | null
  email: string
  role_id: number
  registered: boolean
  source: FaceSource | null
  registered_by_name: string | null
  registered_at: string | null
  updated_at: string | null
}

export interface FaceCheck {
  shift_id: string
  which: 'check_in' | 'check_out'
  outcome: FaceOutcome
  similarity: number | null
  min_similarity: number | null
  reason: string | null
  created_at: string
  guard_user_id: string | null
  guard_name: string | null
  guard_email: string | null
  site_name: string | null
  scheduled_start: string
  scheduled_end: string
  face_on_file: boolean
}

/** Everyone active whose face the signed-in person may change, with what is on file. */
export const listFaces = () => apiClient.get<StaffFace[]>('/api/v1/faces').then((r) => r.data)

export const putFace = (userId: string, file: File, consent: boolean) => {
  const form = new FormData()
  form.append('photo', file)
  form.append('consent', consent ? 'true' : 'false')
  return apiClient
    .put<StaffFace>(`/api/v1/faces/${userId}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // The face model loads on the first photo after the server starts.
      timeout: 60_000,
    })
    .then((r) => r.data)
}

export const deleteFace = (userId: string) => apiClient.delete(`/api/v1/faces/${userId}`).then((r) => r.data)

/** The check-in face checks of the last `days`; `review` is the ones that did not match or could not be checked. */
export const getFaceChecks = (params: { outcome?: FaceOutcome | 'review'; days?: number }) =>
  apiClient.get<FaceCheck[]>('/api/v1/faces/checks', { params }).then((r) => r.data)

/** The photo on file, for an <img>: the token travels in the address, as on the other photo endpoints. */
export const facePhotoUrl = (userId: string, token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/faces/${userId}/photo?token=${token}` : null

export const OUTCOME_LABEL: Record<FaceOutcome, string> = {
  MATCHED: 'Face matched',
  MISMATCH: 'Face did not match',
  NO_FACE: 'No face on file',
  NOT_CHECKED: 'Face not checked',
}

export const OUTCOME_COLOR: Record<FaceOutcome, 'success' | 'error' | 'warning' | 'default'> = {
  MATCHED: 'success',
  MISMATCH: 'error',
  NO_FACE: 'warning',
  NOT_CHECKED: 'default',
}
