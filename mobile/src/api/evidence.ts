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

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000'

export const getEvidence = (params?: { incident_id?: string; limit?: number }) =>
  apiClient
    .get<Evidence[]>('/api/v1/evidence', { params: { limit: 50, ...params } })
    .then((r) => r.data)

// Absolute URL for an evidence file, for an Image source in React Native.
export const evidenceFileUrl = (evidenceId: string): string =>
  `${BASE_URL}/api/v1/evidence/${evidenceId}/file`

// Auth header for evidence file requests — an <Image> cannot carry one itself,
// so the caller passes these as headers on the source.
export const getEvidenceFileHeaders = (): Record<string, string> => {
  const token = apiClient.defaults.headers.common['Authorization']
  return token ? { Authorization: token as string } : {}
}
