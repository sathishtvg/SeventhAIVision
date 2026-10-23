import { apiClient } from './client'

export type MediaType = 'image' | 'video'

export interface Evidence {
  id: string
  detection_id: string | null
  incident_id: string | null
  media_type: MediaType
  storage_path: string
  checksum_sha256: string | null
  captured_at: string
  created_at: string
}

export const getEvidence = (params?: { incident_id?: string; limit?: number }) =>
  apiClient
    .get<Evidence[]>('/api/v1/evidence', { params: { limit: 50, ...params } })
    .then((r) => r.data)

/**
 * Absolute URL for an evidence file, for an Image source in React Native.
 *
 * Built from the server the app is talking to — the one chosen on the sign-in
 * screen — at the moment it is asked for. It used to be built from a constant
 * read once from the build's environment, defaulting to localhost: on a phone
 * that is the phone itself, so every evidence picture was requested from a
 * server that was not there, whichever server the guard had signed in to.
 */
export const evidenceFileUrl = (evidenceId: string): string =>
  `${(apiClient.defaults.baseURL ?? '').replace(/\/$/, '')}/api/v1/evidence/${evidenceId}/file`

/**
 * Address of an evidence picture that an <Image> can load on its own, with a smaller copy for a thumbnail.
 *
 * FIXED 23 October 2026: the Evidence screen loaded the full-size /file with an Authorization header on the
 * Image source, and on the owner's phone no picture appeared - the server answered every one of those
 * requests correctly, so it was the Image that never sent or kept the header. The web has the same problem
 * with <img> and solved it with /image, which takes the token in the address (as the check-in photos and
 * the live streams do) and, given `width`, returns a downscaled JPEG. The phone now uses it too.
 * `null` until the app holds a token.
 */
export const evidenceImageUrl = (evidenceId: string, token: string | null, width?: number): string | null =>
  token
    ? `${(apiClient.defaults.baseURL ?? '').replace(/\/$/, '')}/api/v1/evidence/${evidenceId}/image` +
      `?token=${encodeURIComponent(token)}` + (width ? `&w=${Math.round(width)}` : '')
    : null

// Auth header for evidence file requests — an <Image> cannot carry one itself,
// so the caller passes these as headers on the source.
export const getEvidenceFileHeaders = (): Record<string, string> => {
  const token = apiClient.defaults.headers.common['Authorization']
  return token ? { Authorization: token as string } : {}
}
