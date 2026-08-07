import type { Evidence } from '@/types/api'
import { apiClient } from './client'

export const getEvidence = (limit = 50) =>
  apiClient.get<Evidence[]>('/api/v1/evidence', { params: { limit } }).then((r) => r.data)

// Query-param-JWT auth (same pattern as checkinPhotoUrl / the MJPEG stream
// URLs) — a plain <img src> can't set an Authorization header.
//
// This replaces a bare `/api/v1/evidence/{id}/file` string that had two
// defects, both verified against the running API: it carried no token (that
// endpoint answers 401 to an unauthenticated request) and no baseURL, so it
// resolved against the frontend origin rather than the API. Every evidence
// image in the app has therefore been falling through to its error state.
// Returns null when unauthenticated so the caller can skip the render.
// `width` asks the server to downscale. Pass it for thumbnails — a full frame
// is ~270KB, so a 100-row list that skips this pulls ~27MB to render 40px
// squares. Omit it for the lightbox, where the full image is the point.
export const evidenceImageUrl = (
  evidenceId: string,
  token: string | null,
  width?: number,
) =>
  token
    ? `${apiClient.defaults.baseURL}/api/v1/evidence/${evidenceId}/image?token=${token}` +
      (width ? `&w=${width}` : '')
    : null

export interface DetectionEvidence {
  frame_evidence_id: string | null
  plate_evidence_id: string | null
}

/** Resolve a detection's captures. Used by the visitor entry prompt, which
 *  arrives carrying a detection id rather than evidence ids. */
export const getEvidenceForDetection = (detectionId: string) =>
  apiClient
    .get<DetectionEvidence>(`/api/v1/evidence/by-detection/${detectionId}`)
    .then((r) => r.data)
