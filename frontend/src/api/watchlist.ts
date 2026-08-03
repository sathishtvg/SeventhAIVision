import type { WatchlistEntry, FaceWatchlistEntry, VehicleCategory } from '@/types/api'
import { apiClient } from './client'

/** Display labels + colours for the ten registry categories. Blacklist is the
 * only one that hard-blocks; watchlist is legitimate-but-flagged, which is why
 * they read differently even though both draw attention. */
export const CATEGORY_META: Record<VehicleCategory, { label: string; color: string; hint: string }> = {
  whitelist:  { label: 'Whitelist',  color: '#00E396', hint: 'Standing permission — opens the gate automatically' },
  vip:        { label: 'VIP',        color: '#00E396', hint: 'Standing permission — opens the gate automatically' },
  staff:      { label: 'Staff',      color: '#00E396', hint: 'Standing permission — opens the gate automatically' },
  emergency:  { label: 'Emergency',  color: '#00E396', hint: 'Standing permission — opens the gate automatically' },
  government: { label: 'Government', color: '#00E396', hint: 'Standing permission — opens the gate automatically' },
  visitor:    { label: 'Visitor',    color: '#6C63FF', hint: 'Opens only with a valid booking for today' },
  contractor: { label: 'Contractor', color: '#6C63FF', hint: 'Opens only with a valid booking for today' },
  watchlist:  { label: 'Watchlist',  color: '#FF9800', hint: 'Never auto-opens — always held for an operator' },
  unknown:    { label: 'Unknown',    color: '#7A8195', hint: 'Held for an operator by default' },
  blacklist:  { label: 'Blacklist',  color: '#FF4560', hint: 'Refused entry and raises an alarm' },
}

export const VEHICLE_CATEGORIES = Object.keys(CATEGORY_META) as VehicleCategory[]

export interface PlateRegistryInput {
  plate_number: string
  category: VehicleCategory
  owner_name?: string | null
  company?: string | null
  vehicle_type?: string | null
  vehicle_color?: string | null
  valid_from?: string | null
  valid_to?: string | null
  remarks?: string | null
  reason?: string | null
}

export const getPlateWatchlist = (params?: {
  category?: VehicleCategory
  search?: string
  is_active?: boolean
}) =>
  apiClient
    .get<WatchlistEntry[]>('/api/v1/watchlist/plates', { params: params ?? {} })
    .then((r) => r.data)

export const addPlateEntry = (data: PlateRegistryInput) =>
  apiClient.post<WatchlistEntry>('/api/v1/watchlist/plates', data).then((r) => r.data)

/** Editing was previously impossible — correcting an owner's name or extending
 * a pass meant deleting and re-adding the vehicle, losing its history. */
export const updatePlateEntry = (entryId: string, data: Partial<PlateRegistryInput> & { is_active?: boolean }) =>
  apiClient.put<WatchlistEntry>(`/api/v1/watchlist/plates/${entryId}`, data).then((r) => r.data)

export const deletePlateEntry = (entryId: string) =>
  apiClient.delete(`/api/v1/watchlist/plates/${entryId}`).then((r) => r.data)

export const getFaceWatchlist = () =>
  apiClient.get<FaceWatchlistEntry[]>('/api/v1/watchlist/faces').then((r) => r.data)

export const deleteFaceEntry = (entryId: string) =>
  apiClient.delete(`/api/v1/watchlist/faces/${entryId}`).then((r) => r.data)

export const enrollFace = (formData: FormData): Promise<{ id: string; person_name: string; list_type: string }> =>
  apiClient
    .post('/api/v1/watchlist/faces/enroll', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
    .then((r) => r.data)

export interface BulkImportResult {
  total: number
  imported: number
  duplicates: number
  errors: { row: number; error: string }[]
}

export const bulkImportPlates = (file: File): Promise<BulkImportResult> => {
  const fd = new FormData()
  fd.append('file', file)
  return apiClient
    .post<BulkImportResult>('/api/v1/watchlist/plates/bulk-import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data)
}
