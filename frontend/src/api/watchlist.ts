import type { WatchlistEntry, FaceWatchlistEntry } from '@/types/api'
import { apiClient } from './client'

export const getPlateWatchlist = () =>
  apiClient.get<WatchlistEntry[]>('/api/v1/watchlist/plates').then((r) => r.data)

export const addPlateEntry = (data: { plate_number: string; list_type: 'allow' | 'block'; reason?: string }) =>
  apiClient.post('/api/v1/watchlist/plates', data).then((r) => r.data)

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
