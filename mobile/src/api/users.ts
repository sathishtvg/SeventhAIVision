import { apiClient } from './client'

export interface User {
  id: string
  email: string
  full_name: string | null
  role_id: number
  role_code?: string
  is_active: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export const getUsers = () =>
  apiClient.get<User[]>('/api/v1/users').then((r) => r.data)

export const getUser = (userId: string) =>
  apiClient.get<User>(`/api/v1/users/${userId}`).then((r) => r.data)

export const createUser = (data: {
  email: string
  password: string
  role_id: number
  full_name?: string
}) => apiClient.post<User>('/api/v1/users', data).then((r) => r.data)

export const updateUser = (
  userId: string,
  data: { full_name?: string; role_id?: number; is_active?: boolean },
) => apiClient.put<Partial<User>>(`/api/v1/users/${userId}`, data).then((r) => r.data)

export const deactivateUser = (userId: string) =>
  apiClient.delete(`/api/v1/users/${userId}`).then((r) => r.data)
