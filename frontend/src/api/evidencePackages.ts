/**
 * Evidence Packages API — every call the evidence package screens make, typed to
 * what the backend returns (backend/app/routers/evidence_packages.py).
 *
 * Three things are kept apart here as they are on the server: a THING the
 * platform keeps (a frame, a clip, a recording, a piece of drone media), an
 * ITEM of a package, which only refers to one, and a STEP in the chain of
 * custody. No type here carries where a file is stored: the server never says.
 */
import { apiClient } from './client'

const BASE = '/api/v1/evidence-packages'
const HOLDS = '/api/v1/evidence-holds'

export type EvidenceKind = 'SNAPSHOT' | 'CLIP' | 'RECORDING' | 'DRONE_MEDIA'
export type PackageStatus = 'DRAFT' | 'SEALED'
export type ItemState = 'SHOWN' | 'NOT_PERMITTED' | 'NOT_AVAILABLE'
export type CustodyStep =
  | 'CAPTURED' | 'COLLECTED' | 'REMOVED' | 'VIEWED' | 'ACCESSED' | 'SEALED' | 'LOCKED' | 'UNLOCKED' | 'EXPORTED'
  | 'DOWNLOADED' | 'SHARED' | 'RELEASED'

export interface Paged<T> { items: T[]; total: number; limit: number; offset: number; has_more: boolean }

/** One thing the platform keeps, as this reader may see it now. */
export interface Thing {
  kind: EvidenceKind
  id: string
  what: string
  captured_at: string
  ended_at?: string | null
  media_type: 'image' | 'video'
  site_id: string | null
  site_name: string | null
  camera_id: string | null
  camera_name: string | null
  /** As the platform recorded it. Null when it recorded none. */
  checksum_sha256: string | null
  checksum_status?: string | null
  /** 'central', or 'local' when it is still at the site. */
  kept: string | null
  size_bytes: number | null
  duration_seconds: number | null
  /** The permission its own screen asks for, and whether this reader holds it. */
  needs: string
  may_open: boolean
  served_at: { path: string; token_in_query: boolean }
  /** How far into a recording the record it goes with is. */
  offset_seconds?: number
  /** Which record of the investigation it belongs to. */
  goes_with?: { kind: string; id: string }
}

export interface PackageRow {
  id: string
  package_number: string
  title: string
  purpose: string
  status: PackageStatus
  site_id: string | null
  site_name: string | null
  investigation_id: string | null
  investigation_number: string | null
  incident_id: string | null
  created_by_user_id: string | null
  created_by_name: string | null
  created_at: string
  sealed_by_user_id: string | null
  sealed_by_name: string | null
  sealed_at: string | null
  manifest_sha256: string | null
  updated_at: string
}

export interface PackageListRow extends PackageRow { items: number; holds_in_force: number }

export interface PackageItem {
  id: string
  kind: EvidenceKind
  ref_id: string
  captured_at: string
  site_id: string | null
  camera_id: string | null
  /** The checksum the platform had recorded when the item was added. */
  checksum_sha256: string | null
  note: string | null
  added_by_user_id: string | null
  added_by_name: string | null
  added_at: string
  state: ItemState
  thing: Thing | null
  /** Under a hold in force: the retention jobs leave it alone. */
  held: boolean
  /** The platform now records a different checksum than when it was added. */
  checksum_changed: boolean
}

export interface PackageDetail extends PackageRow {
  /** Whether a sealed package's manifest still has the hash it was sealed with. Null for a draft. */
  intact: boolean | null
  items: PackageItem[]
  counts: { items: number; held: number; not_shown: number; without_checksum: number }
  can: { manage: boolean; export: boolean; hold: boolean; custody: boolean }
  max_items: number
}

export interface Candidates {
  /** How many records of the investigation or incident were looked under. */
  records: number
  items: Thing[]
  already_in: number
  /** Kinds this reader could not be shown, each with the reason. */
  not_looked_for: string[]
}

export interface Step {
  step: CustodyStep
  at: string
  kind: EvidenceKind | null
  ref_id: string | null
  actor_name: string | null
  actor_role: number | null
  reason: string | null
  detail: Record<string, unknown>
  source: string
}

export interface Custody {
  package_number: string
  status: PackageStatus
  manifest_sha256: string | null
  chain: Step[]
  note: string
}

export interface Hold {
  id: string
  kind: EvidenceKind
  ref_id: string
  site_id: string | null
  site_name: string | null
  package_id: string | null
  package_number: string | null
  reason: string
  placed_by_name: string | null
  placed_at: string
  released_by_name: string | null
  released_at: string | null
  release_reason: string | null
}

/** What an export found, as the server reports it beside the file. */
export interface ExportResult {
  file: Blob
  filename: string
  included: number
  left_out: number
  verified: number
  mismatched: number
  unverifiable: number
}

export const listPackages = (params: {
  status?: PackageStatus; site_id?: string; investigation_id?: string; incident_id?: string; q?: string
  limit?: number; offset?: number
}) => apiClient.get<Paged<PackageListRow>>(BASE, { params }).then((r) => r.data)

export const createPackage = (body: { title: string; purpose: string; investigation_id?: string; incident_id?: string }) =>
  apiClient.post<{ id: string; package_number: string }>(BASE, body).then((r) => r.data)

export const getPackage = (id: string) => apiClient.get<PackageDetail>(`${BASE}/${id}`).then((r) => r.data)

export const getCandidates = (id: string) => apiClient.get<Candidates>(`${BASE}/${id}/candidates`).then((r) => r.data)

export const addItems = (id: string, things: Pick<Thing, 'kind' | 'id' | 'captured_at'>[], note?: string) =>
  apiClient.post<{ added: { kind: EvidenceKind; id: string }[]; already_in: { kind: EvidenceKind; id: string }[] }>(
    `${BASE}/${id}/items`,
    { items: things.map((t) => ({ kind: t.kind, id: t.id, captured_at: t.captured_at })), note: note || undefined },
  ).then((r) => r.data)

export const removeItem = (id: string, itemId: string) =>
  apiClient.delete(`${BASE}/${id}/items/${itemId}`).then((r) => r.data)

export const sealPackage = (id: string) =>
  apiClient.post<{ status: 'SEALED'; manifest_sha256: string; items: number; without_checksum: number; holds: number }>(
    `${BASE}/${id}/seal`).then((r) => r.data)

const count = (headers: Record<string, unknown>, name: string) => Number(headers[`x-evidence-${name}`] ?? 0)

/** A refusal to a request that asked for a file comes back as a file. Read
 *  it, so that the server's own words reach the person. */
async function refused(err: unknown): Promise<never> {
  const response = (err as { response?: { data?: unknown } })?.response
  if (response && response.data instanceof Blob) {
    try {
      response.data = JSON.parse(await response.data.text())
    } catch {
      response.data = undefined
    }
  }
  throw err
}

export const exportPackage = (id: string, number: string, reason: string, markedCopies: boolean): Promise<ExportResult> =>
  apiClient.post<Blob>(`${BASE}/${id}/export`, { reason, marked_copies: markedCopies }, { responseType: 'blob' })
    .then((r) => ({
      file: r.data, filename: `${number}.zip`, included: count(r.headers, 'included'),
      left_out: count(r.headers, 'left-out'), verified: count(r.headers, 'verified'),
      mismatched: count(r.headers, 'mismatched'), unverifiable: count(r.headers, 'unverifiable'),
    }))
    .catch(refused)

export const downloadOriginal = (id: string, itemId: string) =>
  apiClient.get<Blob>(`${BASE}/${id}/items/${itemId}/file`, { responseType: 'blob' }).then((r) => r.data)
    .catch(refused)

export const recordDisclosure = (id: string, body: {
  step: 'SHARED' | 'RELEASED'; recipient: string; organisation?: string; reason: string
}) => apiClient.post(`${BASE}/${id}/disclosures`, body).then((r) => r.data)

export const releasePackageHolds = (id: string, reason: string) =>
  apiClient.post<{ released: number }>(`${BASE}/${id}/release-holds`, { reason }).then((r) => r.data)

export const getCustody = (id: string) => apiClient.get<Custody>(`${BASE}/${id}/custody`).then((r) => r.data)

export const listHolds = (params: { in_force?: boolean; kind?: EvidenceKind; limit?: number; offset?: number }) =>
  apiClient.get<Paged<Hold>>(HOLDS, { params }).then((r) => r.data)

export const releaseHold = (holdId: string, reason: string) =>
  apiClient.post(`${HOLDS}/${holdId}/release`, { reason }).then((r) => r.data)

/** Hand a file the server sent to the browser to save. */
export function saveFile(file: Blob, filename: string): void {
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

/** What went wrong, in the server's words where it gave any. */
export function apiError(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many exports in a minute. Wait a moment and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  if (d && typeof d === 'object') return (d as { message?: string }).message ?? 'That could not be done.'
  return e?.message ?? 'Something went wrong.'
}
