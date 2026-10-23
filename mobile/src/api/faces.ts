/**
 * My registered face, for check-in (docs/plans/2026-10-23-staff-face-registration-design.md).
 *
 * A person registers their own face once, from a live selfie; after that only a
 * supervisor or an admin can change it, on the web. A check-in selfie is then
 * compared with it, and the outcome recorded for a supervisor to review: it never
 * stops the check-in.
 */
import { apiClient } from './client'

export interface MyFace {
  registered: boolean
  registered_at: string | null
  updated_at: string | null
  source: 'SELFIE' | 'UPLOAD' | null
  registered_by_name: string | null
  /** True only while nothing is on file: registering yourself is a one-time thing. */
  may_register: boolean
  /** Who to ask to change it, in words: "a supervisor or an admin". */
  who_may_change: string
}

export type FaceOutcome = 'MATCHED' | 'MISMATCH' | 'NO_FACE' | 'NOT_CHECKED'

export interface FaceCheck {
  which: 'check_in' | 'check_out'
  outcome: FaceOutcome
  similarity: number | null
  min_similarity: number | null
  reason: string | null
  created_at: string
}

/** Registration uploads a selfie and the server checks it twice (liveness, then the face); the first check after
 *  the server starts also loads the face models. The app's usual 15 s would give up first. */
export const FACE_UPLOAD_TIMEOUT_MS = 60_000

export const getMyFace = () => apiClient.get<MyFace>('/api/v1/faces/me').then((r) => r.data)

export const registerMyFace = (photoUri: string, consent: boolean) => {
  const form = new FormData()
  form.append('photo', { uri: photoUri, name: 'face.jpg', type: 'image/jpeg' } as any)
  form.append('consent', consent ? 'true' : 'false')
  return apiClient
    .post<MyFace>('/api/v1/faces/me', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: FACE_UPLOAD_TIMEOUT_MS,
    })
    .then((r) => r.data)
}

export const getMyFaceChecks = (shiftId: string) =>
  apiClient
    .get<FaceCheck[]>('/api/v1/faces/me/checks', { params: { shift_id: shiftId } })
    .then((r) => r.data)

/** The photo on file, for an <Image>: the token travels in the address (an Image cannot send a header). */
export const myFacePhotoUrl = (userId: string, token: string | null): string | null =>
  token
    ? `${(apiClient.defaults.baseURL ?? '').replace(/\/$/, '')}/api/v1/faces/${userId}/photo?token=${encodeURIComponent(token)}`
    : null

/** What the guard is told after checking in or out, or null when there is nothing to say. */
export function faceCheckMessage(check: FaceCheck | undefined): { title: string; body: string } | null {
  if (!check) return null
  if (check.outcome === 'MISMATCH') {
    return {
      title: 'Face did not match',
      body: 'Your face did not match the one on file. Your supervisor will review this check-in.',
    }
  }
  if (check.outcome === 'NO_FACE') {
    return {
      title: 'No face registered',
      body: 'You have not registered your face yet. Register it in More > My Face before your next check-in.',
    }
  }
  return null
}
