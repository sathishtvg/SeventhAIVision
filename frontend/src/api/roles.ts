import { apiClient } from './client'

export interface RoleRow {
  id: number
  code?: string
  name: string
  description: string | null
  is_custom: boolean
  permission_codes: string[]
  user_count: number
}

export interface PermissionRow {
  code: string
  category: string
  description: string | null
}

export const listRoles = () =>
  apiClient.get<RoleRow[]>('/api/v1/roles').then((r) => r.data)

export const listPermissionCatalogue = () =>
  apiClient.get<PermissionRow[]>('/api/v1/roles/permissions').then((r) => r.data)

export const createRole = (data: { name: string; description?: string; permission_codes: string[] }) =>
  apiClient.post<RoleRow>('/api/v1/roles', data).then((r) => r.data)

export const updateRole = (
  id: number,
  data: { name?: string; description?: string; permission_codes?: string[] },
) => apiClient.put(`/api/v1/roles/${id}`, data).then((r) => r.data)

export const deleteRole = (id: number) =>
  apiClient.delete(`/api/v1/roles/${id}`).then((r) => r.data)
