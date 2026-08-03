import { apiClient } from './client'
import type { WallLayoutCell } from './wallLayouts'

export interface WallProfileScreen {
  id: string
  screen_index: number
  name: string
  grid_size: number
  cells: WallLayoutCell[]
  analytics_modules: string[]
}

export interface WallProfile {
  id: string
  user_id: string
  name: string
  screen_count: number
  is_shared: boolean
  is_mine: boolean
  owner_name: string
  screens: WallProfileScreen[]
  created_at: string
  updated_at: string
}

export interface WallProfileScreenInput {
  grid_size: number
  cells: WallLayoutCell[]
  analytics_modules: string[]
}

export const listWallProfiles = () =>
  apiClient.get<WallProfile[]>('/api/v1/wall-profiles').then((r) => r.data)

export const createWallProfile = (data: {
  name: string
  is_shared?: boolean
  screens: WallProfileScreenInput[]
}) => apiClient.post<WallProfile>('/api/v1/wall-profiles', data).then((r) => r.data)

export const updateWallProfile = (
  id: string,
  data: { name?: string; is_shared?: boolean; screens?: WallProfileScreenInput[] }
) => apiClient.put<WallProfile>(`/api/v1/wall-profiles/${id}`, data).then((r) => r.data)

export const addWallProfileScreen = (id: string, screen: WallProfileScreenInput) =>
  apiClient.post<WallProfile>(`/api/v1/wall-profiles/${id}/screens`, screen).then((r) => r.data)

export const deleteWallProfile = (id: string) =>
  apiClient.delete(`/api/v1/wall-profiles/${id}`).then((r) => r.data)
