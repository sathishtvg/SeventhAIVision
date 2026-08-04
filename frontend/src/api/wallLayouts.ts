import { apiClient } from './client'

export interface WallLayoutCell {
  camera_id: string
  stream_id: string
  camera_name: string
  site_name?: string | null
  /** Per-camera analytics override. Undefined/null = inherit the screen's
   * (or the wall-wide) selection; an array = show exactly these on this
   * camera. Stored inside the cells JSONB, so no schema change. */
  analytics_modules?: string[] | null
}

export interface WallLayout {
  id: string
  user_id: string
  name: string
  grid_size: number
  cells: WallLayoutCell[]
  is_shared: boolean
  is_mine: boolean
  owner_name: string
  created_at: string
  updated_at: string
}

export const listWallLayouts = () =>
  apiClient.get<WallLayout[]>('/api/v1/wall-layouts').then((r) => r.data)

export const createWallLayout = (data: {
  name: string
  grid_size: number
  cells: WallLayoutCell[]
  is_shared?: boolean
}) => apiClient.post<WallLayout>('/api/v1/wall-layouts', data).then((r) => r.data)

export const updateWallLayout = (
  id: string,
  data: { name?: string; grid_size?: number; cells?: WallLayoutCell[]; is_shared?: boolean }
) => apiClient.put<WallLayout>(`/api/v1/wall-layouts/${id}`, data).then((r) => r.data)

export const deleteWallLayout = (id: string) =>
  apiClient.delete(`/api/v1/wall-layouts/${id}`).then((r) => r.data)
