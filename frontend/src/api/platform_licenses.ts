import { apiClient } from './client'

export interface ProductModule {
  module_code: string
  name: string
  description: string | null
  is_default_enabled: boolean
}

export interface Product {
  id: string
  name: string
  description: string | null
  is_active: boolean
  modules: ProductModule[]
}

export interface TenantProductModule {
  module_code: string
  name: string
  is_enabled: boolean
}

export interface TenantProduct {
  product_id: string
  name: string
  is_licensed: boolean
  is_enabled: boolean
  licensed_at: string | null
  expires_at: string | null
  modules: TenantProductModule[]
}

export const listProducts = () =>
  apiClient.get<Product[]>('/api/v1/platform/products').then((r) => r.data)

export const getProduct = (productId: string) =>
  apiClient.get<Product>(`/api/v1/platform/products/${productId}`).then((r) => r.data)

export const getTenantProducts = (tenantId: string) =>
  apiClient.get<TenantProduct[]>(`/api/v1/platform/tenants/${tenantId}/products`).then((r) => r.data)

export const assignProduct = (tenantId: string, productId: string, isEnabled: boolean) =>
  apiClient
    .post<{ product_id: string; is_enabled: boolean }>(
      `/api/v1/platform/tenants/${tenantId}/products/${productId}`,
      { is_enabled: isEnabled }
    )
    .then((r) => r.data)

export const revokeProduct = (tenantId: string, productId: string) =>
  apiClient.delete(`/api/v1/platform/tenants/${tenantId}/products/${productId}`).then((r) => r.data)

export const toggleProductModule = (
  tenantId: string,
  productId: string,
  moduleCode: string,
  isEnabled: boolean
) =>
  apiClient
    .put(`/api/v1/platform/tenants/${tenantId}/products/${productId}/modules/${moduleCode}`, {
      is_enabled: isEnabled,
    })
    .then((r) => r.data)

export const PRODUCT_LABELS: Record<string, string> = {
  seventh_ai_vision: 'Seventh AI Vision',
  shift_secure: 'ShiftSecure',
}

export const PLATFORM_MODULE_LABELS: Record<string, string> = {
  vms: 'Video Management',
  detections: 'AI Detections',
  analytics: 'Analytics',
  reports: 'Reports',
  client_portal: 'Client Portal',
  mobile_app: 'Mobile App',
  guard_management: 'Guard Management',
  patrol: 'Patrol Routes',
  occurrence_book: 'Occurrence Book',
  dispatch: 'Dispatch',
  visitor_management: 'Visitor Management',
  timesheet: 'Timesheet & Payroll',
}
