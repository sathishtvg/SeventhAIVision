import type { ModuleLicense } from '@/types/api'
import { apiClient } from './client'

export const ALL_AI_MODULES = [
  'lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior',
  'tampering', 'abandoned', 'fall',
] as const

export type AiModuleType = (typeof ALL_AI_MODULES)[number]

export const MODULE_LABELS: Record<AiModuleType, string> = {
  lpr: 'License Plate Recognition',
  face: 'Face Recognition',
  intrusion: 'Intrusion Detection',
  ppe: 'PPE Detection',
  crowd: 'Crowd Density',
  fire_smoke: 'Fire & Smoke Detection',
  weapon: 'Weapon Detection',
  behavior: 'Behavior Analysis',
  tampering: 'Camera Tampering',
  abandoned: 'Abandoned Objects',
  fall: 'Slip / Fall Detection',
}

export const getTenantLicenses = (tenantId: string) =>
  apiClient.get<ModuleLicense[]>(`/api/v1/licenses/${tenantId}`).then((r) => r.data)

/** Which modules the caller's own tenant has licensed — any authenticated
 * user (unlike getTenantLicenses, which requires license:manage). */
export const getMyEnabledModules = () =>
  apiClient.get<{ modules: string[] }>('/api/v1/licenses/me/enabled-modules').then((r) => r.data.modules)

export const upsertLicense = (
  tenantId: string,
  moduleType: string,
  data: { is_enabled: boolean; max_cameras?: number; expires_at?: string; notes?: string }
) => apiClient.put(`/api/v1/licenses/${tenantId}/${moduleType}`, data).then((r) => r.data)

export const revokeLicense = (tenantId: string, moduleType: string) =>
  apiClient.delete(`/api/v1/licenses/${tenantId}/${moduleType}`).then((r) => r.data)
