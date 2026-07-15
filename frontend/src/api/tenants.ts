import type { Tenant, User } from '@/types/api'
import { apiClient } from './client'

export const getTenants = () =>
  apiClient.get<Tenant[]>('/api/v1/tenants').then((r) => r.data)

export const getTenant = (id: string) =>
  apiClient.get<Tenant>(`/api/v1/tenants/${id}`).then((r) => r.data)

export const createTenant = (data: {
  name: string
  slug: string
  subdomain?: string
  timezone?: string
  branding?: Record<string, string>
  admin_email?: string
  admin_password?: string
  admin_full_name?: string
}) => apiClient.post<Tenant>('/api/v1/tenants', data).then((r) => r.data)

export const updateTenant = (id: string, data: {
  name?: string
  slug?: string
  subdomain?: string
  custom_domain?: string
  timezone?: string
  branding?: Record<string, string>
  is_active?: boolean
}) => apiClient.put<Tenant>(`/api/v1/tenants/${id}`, data).then((r) => r.data)

export const deactivateTenant = (id: string) =>
  apiClient.delete(`/api/v1/tenants/${id}`)

export const createTenantUser = (tenantId: string, data: {
  email: string
  password: string
  role_id?: number
  full_name?: string
}) => apiClient.post<User>(`/api/v1/tenants/${tenantId}/users`, data).then((r) => r.data)
