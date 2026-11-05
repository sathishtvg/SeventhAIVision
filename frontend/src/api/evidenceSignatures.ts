/**
 * Evidence signatures (backend/app/routers/evidence_signatures.py; Phase 7 of the known limits, 5 November 2026).
 *
 * An export is checked by uploading it: the server checks it in a temporary file it deletes, and says whether the
 * platform signed it, with which key, whether every file is the one signed, and whether its record of the sealed
 * package agrees. Nothing of the upload is kept.
 */
import { apiClient } from './client'

const BASE = '/api/v1/evidence-signatures'

export type SealState = 'SIGNED' | 'NOT_SIGNED' | 'BEFORE_SIGNING'

/** What a sealed package's page says of its seal. */
export interface SealSignature {
  state: SealState
  words: string
  key_fingerprint: string | null
  key_spaced: string | null
  signed_at: string | null
}

export interface CheckLine {
  what: string
  /** True, false, or null when it could not be said either way. */
  ok: boolean | null
  detail: string
}

export interface CheckResult {
  genuine: boolean
  signed: boolean
  key_fingerprint: string | null
  key_spaced: string | null
  package_number: string | null
  exported_at: string | null
  exported_by: string | null
  seal: SealState | null
  checks: CheckLine[]
  /** What the platform knows: whether the key is this installation's, whether its record of the package agrees. */
  platform: CheckLine[]
  note: string
  kept: false
}

export interface SigningKey {
  fingerprint: string
  spaced: string
  source: 'SECRET' | 'FILE'
  first_used_at: string
  retired_at: string | null
  in_use: boolean
}

export interface SigningKeys {
  keys: SigningKey[]
  in_use: string | null
  not_signing_because: string | null
  note: string
}

export const getSigningKeys = () => apiClient.get<SigningKeys>(`${BASE}/keys`).then((r) => r.data)

export const checkExport = (file: File) => {
  const body = new FormData()
  body.append('file', file)
  return apiClient.post<CheckResult>(`${BASE}/check`, body, { timeout: 10 * 60_000 }).then((r) => r.data)
}
