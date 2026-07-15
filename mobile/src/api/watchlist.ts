import { apiClient } from './client'

export type ListType = 'allow' | 'block'

export interface WatchlistEntry {
  id: string
  plate_number: string
  list_type: ListType
  reason: string | null
  is_active: boolean
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface FaceWatchlistEntry {
  id: string
  person_name: string
  list_type: ListType
  is_active: boolean
  expires_at: string | null
  created_at: string
  updated_at: string
}

// Plate watchlist
export const getPlateWatchlist = () =>
  apiClient.get<WatchlistEntry[]>('/api/v1/watchlist/plates').then((r) => r.data)

export const addPlateEntry = (data: {
  plate_number: string
  list_type: ListType
  reason?: string
  expires_at?: string
}) => apiClient.post<WatchlistEntry>('/api/v1/watchlist/plates', data).then((r) => r.data)

export const updatePlateEntry = (id: string, data: { reason?: string; is_active?: boolean; expires_at?: string }) =>
  apiClient.put<WatchlistEntry>(`/api/v1/watchlist/plates/${id}`, data).then((r) => r.data)

export const deletePlateEntry = (id: string) =>
  apiClient.delete(`/api/v1/watchlist/plates/${id}`)

// Face watchlist
export const getFaceWatchlist = () =>
  apiClient.get<FaceWatchlistEntry[]>('/api/v1/watchlist/faces').then((r) => r.data)

export const deleteFaceEntry = (id: string) =>
  apiClient.delete(`/api/v1/watchlist/faces/${id}`)
