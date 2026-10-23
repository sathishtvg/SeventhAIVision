/**
 * Footage Review — videos uploaded to be searched and reviewed (/api/v1/footage).
 *
 * A video arrives in parts, in order: the server is told the file's name and
 * size, answers with an id and a part size, and each part is then sent at the
 * offset the server has reached. If the server has reached a different offset
 * it says which, and the upload carries on from there - so an upload that
 * stopped carries on where it was, from this page or after a reload with the
 * same file chosen again.
 */
import { apiClient } from './client'

export type FootageStatus = 'UPLOADING' | 'QUEUED' | 'PROCESSING' | 'READY' | 'FAILED'

export interface FootageRules {
  part_size: number
  max_bytes: number
  max_seconds: number
  extensions: string[]
  retention_days: number
}

export interface FootageMay {
  upload: boolean
  review: boolean
  delete: boolean
  hold: boolean
}

export interface FootageVideo {
  id: string
  source: 'UPLOAD'
  title: string
  original_filename: string
  content_type: string | null
  size_bytes: number
  received_bytes: number
  part_size: number
  site_id: string | null
  site_name: string | null
  place: string | null
  filmed_at: string | null
  duration_seconds: number | null
  width: number | null
  height: number | null
  frame_rate: number | null
  status: FootageStatus
  status_label: string
  progress: number
  error: string | null
  sha256: string | null
  uploaded_by_user_id: string | null
  uploaded_by_name: string | null
  created_at: string
  updated_at: string
  upload_completed_at: string | null
  processed_at: string | null
  expires_at: string
  held: boolean
  hold_id: string | null
  hold_reason: string | null
  hold_placed_at: string | null
  hold_placed_by_name: string | null
  /** Days until the 30-day removal; null while a hold keeps it. */
  deleted_in_days: number | null
  codec?: string | null
  frames_indexed?: number
  index_model?: string | null
  /** What a browser can play: the original, a copy the worker made, or neither. */
  playable?: 'ORIGINAL' | 'PROXY' | 'NONE' | null
}

export interface FootagePage {
  items: FootageVideo[]
  total: number
  limit: number
  offset: number
  may: FootageMay
  rules: FootageRules
}

export interface StartUpload {
  filename: string
  size_bytes: number
  title?: string
  place?: string
  site_id?: string | null
  filmed_at?: string | null
}

export const listFootage = (params: { q?: string; status?: FootageStatus; site_id?: string; limit?: number; offset?: number }) =>
  apiClient.get<FootagePage>('/api/v1/footage', { params }).then((r) => r.data)

export const getFootage = (id: string) =>
  apiClient.get<FootageVideo & { may: FootageMay }>(`/api/v1/footage/${id}`).then((r) => r.data)

export const startUpload = (body: StartUpload) =>
  apiClient.post<FootageVideo & { rules: FootageRules }>('/api/v1/footage', body).then((r) => r.data)

export const uploadState = (id: string) =>
  apiClient.get<{ id: string; status: FootageStatus; size_bytes: number; received_bytes: number; part_size: number }>(
    `/api/v1/footage/${id}/upload`).then((r) => r.data)

export const sendPart = (id: string, offset: number, part: Blob, signal?: AbortSignal) =>
  apiClient.put<{ id: string; received_bytes: number; size_bytes: number; complete: boolean }>(
    `/api/v1/footage/${id}/content`, part,
    { params: { offset }, headers: { 'Content-Type': 'application/octet-stream' }, signal },
  ).then((r) => r.data)

export const finishUpload = (id: string) =>
  apiClient.post<FootageVideo & { announced: boolean }>(`/api/v1/footage/${id}/complete`).then((r) => r.data)

export const cancelUpload = (id: string) =>
  apiClient.delete<{ cancelled: boolean }>(`/api/v1/footage/${id}/upload`).then((r) => r.data)

export const holdFootage = (id: string, reason: string) =>
  apiClient.post<{ id: string; placed_at: string }>(`/api/v1/footage/${id}/hold`, { reason }).then((r) => r.data)

export const deleteFootage = (id: string, reason: string) =>
  apiClient.delete<{ deleted: boolean }>(`/api/v1/footage/${id}`, { data: { reason } }).then((r) => r.data)

/** The extension the server accepts, lower case with its dot, or null. */
export function extensionOf(name: string, allowed: string[]): string | null {
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  return allowed.includes(ext) ? ext : null
}

/** What stops this file being uploaded, said to the person, or null when nothing does. */
export function refusalOf(file: File, rules: FootageRules): string | null {
  if (!extensionOf(file.name, rules.extensions)) return 'Only MP4, MOV, MKV and AVI videos can be uploaded.'
  if (file.size <= 0) return 'This file is empty.'
  if (file.size > rules.max_bytes) return `A video may be at most ${Math.floor(rules.max_bytes / 1024 ** 3)} GB.`
  return null
}

export function sizeText(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} bytes`
}

/** The server's offset when it refused a part sent at another one, or null. */
function offsetFromRefusal(err: unknown): number | null {
  const res = (err as { response?: { status?: number; data?: { detail?: { received_bytes?: unknown } } } })?.response
  const at = res?.status === 409 ? res.data?.detail?.received_bytes : undefined
  return typeof at === 'number' ? at : null
}

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms))

/**
 * Send the rest of the file, from the offset the server has, then finish the
 * upload. `onProgress` is told the bytes the server has. A part that fails for
 * want of a connection is tried again, waiting longer each time, up to
 * `tries` times; a refusal at another offset carries on from the server's.
 */
export async function sendRest(
  video: { id: string; size_bytes: number; received_bytes: number; part_size: number },
  file: Blob,
  onProgress: (received: number) => void,
  { signal, tries = 5, pause = 2000, send = sendPart, finish = finishUpload }: {
    signal?: AbortSignal; tries?: number; pause?: number; send?: typeof sendPart; finish?: typeof finishUpload
  } = {},
): Promise<FootageVideo> {
  let offset = video.received_bytes
  onProgress(offset)
  let failures = 0
  while (offset < video.size_bytes) {
    if (signal?.aborted) throw new DOMException('The upload was stopped.', 'AbortError')
    const part = file.slice(offset, Math.min(offset + video.part_size, video.size_bytes))
    try {
      const answer = await send(video.id, offset, part, signal)
      offset = answer.received_bytes
      failures = 0
      onProgress(offset)
    } catch (err) {
      const at = offsetFromRefusal(err)
      if (at !== null) { offset = at; onProgress(offset); continue }
      const status = (err as { response?: { status?: number } })?.response?.status
      if (signal?.aborted || (status !== undefined && status < 500)) throw err
      failures += 1
      if (failures >= tries) throw err
      await wait(pause * failures)
    }
  }
  return finish(video.id)
}

// ── phase 2: searching by description, frames, playing, analysing again ─────

export type Strength = 'HIGH' | 'MEDIUM' | 'LOW'

export interface SearchMoment {
  video_id: string
  title: string
  site_name: string | null
  place: string | null
  /** Seconds into the video: where the moment begins and ends, and its best frame. */
  start: number
  end: number
  best: number
  likeness: number
  /** The model's own probability that the best frame shows what was described, 0 to 1. */
  match: number
  label: Strength
  label_words: string
  /** When the best frame was filmed, if the video's start time is known. */
  at: string | null
}

export interface SearchAnswer {
  text: string
  model_version: string
  moments: SearchMoment[]
  strong: boolean
  videos_searched: number
  frames_searched: number
  not_searched: number
  thresholds: { strong: number; possible: number; weak: number }
}

export interface SearchAsk {
  text: string
  video_id?: string
  site_id?: string
  filmed_from?: string | null
  filmed_to?: string | null
  limit?: number
}

export const searchFootage = (body: SearchAsk) =>
  apiClient.post<SearchAnswer>('/api/v1/footage/search', body).then((r) => r.data)

export const reanalyse = (id: string) =>
  apiClient.post<FootageVideo & { announced: boolean }>(`/api/v1/footage/${id}/reanalyse`).then((r) => r.data)

const base = () => apiClient.defaults.baseURL ?? ''

/** The frame kept for one second, for an <img>: the token goes in the address. */
export const frameUrl = (id: string, second: number, token: string | null) =>
  `${base()}/api/v1/footage/${id}/frames/${Math.max(0, Math.floor(second))}.jpg?token=${encodeURIComponent(token ?? '')}`

/** The video for a <video>: the original, or the copy the worker made a browser can play. */
export const videoUrl = (id: string, token: string | null) =>
  `${base()}/api/v1/footage/${id}/video?token=${encodeURIComponent(token ?? '')}`

/** Seconds as the player shows them: 1:05, or 1:02:03. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const rest = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`
}

// ── phase 3: what the worker found, on the timeline ─────────────────────────

export type FindingKind = 'PEOPLE' | 'VEHICLES' | 'ALTERCATION'

export interface Finding {
  id: string
  kind: FindingKind
  kind_words: string
  start_second: number
  end_second: number
  best_second: number
  score: number
  /** How many were seen at once, at most (people and vehicles). */
  most: number | null
  reasons: Record<string, unknown>
  model_version: string
  verdict: 'CONFIRMED' | 'REJECTED' | null
  verdict_words: string | null
  verdict_note: string | null
  verdict_at: string | null
  verdict_by_name: string | null
  /** When the best second was filmed, if the video's start time is known. */
  at: string | null
}

export interface FindingsAnswer {
  video_id: string
  status: FootageStatus
  duration_seconds: number | null
  findings: Finding[]
  /** [second, people, vehicles] for each second in which anything was counted. */
  counts: [number, number, number][]
  may: FootageMay
}

export const getFindings = (id: string) =>
  apiClient.get<FindingsAnswer>(`/api/v1/footage/${id}/findings`).then((r) => r.data)
