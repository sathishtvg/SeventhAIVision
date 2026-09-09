import { apiClient } from './client'

/** The platform owner's figures. Every one of these excludes Seventh AI's own
 *  tenant: counting yourself as a customer is how a dashboard starts lying. */
export interface PlatformDashboard {
  tenants_total: number
  tenants_active: number
  tenants_trial: number
  tenants_suspended: number
  tenants_expired: number
  tenants_new_this_month: number
  users_total: number
  users_active: number
  users_new_this_month: number
  users_active_today: number
  sites_total: number
  sites_active: number
  sites_new_this_month: number
  cameras_total: number
  cameras_active: number
  modules_licensed: number
  modules_active: number
  errors_critical: number
  errors_warning: number
  errors_resolved_today: number
}

export interface PlatformRevenue {
  mrr: number
  arr: number
  subscriptions_active: number
  subscriptions_trial: number
  renewals_next_30_days: number
}

export interface TenantUsageRow {
  id: string
  name: string
  slug: string
  status: string
  is_active: boolean
  created_at: string
  contact_name: string | null
  contact_email: string | null
  trial_ends_at: string | null
  users: number
  users_active: number
  sites: number
  cameras: number
  modules: number
  last_login_at: string | null
  subscription_status: string | null
  renews_at: string | null
  plan_name: string | null
}

export interface TenantUserStats {
  tenant: { id: string; name: string; slug: string; status: string }
  total: number
  active: number
  inactive: number
  created_this_month: number
  active_last_7_days: number
  last_login_at: string | null
  by_role: { role_id: number; role_name: string | null; count: number }[]
}

export interface TenantDetail {
  tenant: Record<string, unknown> & { id: string; name: string; slug: string; status: string }
  users: number
  sites: number
  cameras: number
  cameras_active: number
  recordings: number
  modules: {
    module_type: string
    is_enabled: boolean
    max_cameras: number | null
    expires_at: string | null
  }[]
  subscription: {
    status: string
    plan_name: string | null
    price_monthly: string | null
    price_yearly: string | null
    current_period_end: string | null
    max_cameras: number | null
    max_sites: number | null
    max_users: number | null
  } | null
}

/** One distinct problem, not one occurrence. A bad deploy is a single row
 *  saying 40,000, rather than 40,000 rows nobody scrolls through. */
export interface PlatformError {
  id: string
  fingerprint: string
  service: string
  severity: 'critical' | 'warning' | 'info'
  error_type: string | null
  message: string | null
  method: string | null
  path_pattern: string | null
  first_seen_at: string
  last_seen_at: string
  occurrence_count: number
  status: 'open' | 'acknowledged' | 'resolved'
  resolution: string | null
  resolved_at: string | null
  tenants_affected: number
}

export interface PlatformErrorDetail {
  error: PlatformError
  recent_events: {
    id: string
    occurred_at: string
    tenant_id: string | null
    tenant_name: string | null
    status_code: number | null
    request_id: string | null
    path: string | null
    stack: string | null
  }[]
  tenants_affected: { id: string; name: string; occurrences: number }[]
}

export const getPlatformDashboard = () =>
  apiClient.get<PlatformDashboard>('/api/v1/platform/dashboard').then((r) => r.data)

export const getPlatformRevenue = () =>
  apiClient.get<PlatformRevenue>('/api/v1/platform/revenue').then((r) => r.data)

export const getTenantUsage = () =>
  apiClient.get<TenantUsageRow[]>('/api/v1/platform/tenants').then((r) => r.data)

export const getTenantUserStats = (tenantId: string) =>
  apiClient
    .get<TenantUserStats>(`/api/v1/platform/tenants/${tenantId}/users`)
    .then((r) => r.data)

export const getTenantDetail = (tenantId: string) =>
  apiClient
    .get<TenantDetail>(`/api/v1/platform/tenants/${tenantId}/usage`)
    .then((r) => r.data)

export const getPlatformErrors = (params?: { status?: string; severity?: string }) =>
  apiClient
    .get<PlatformError[]>('/api/v1/platform/errors', { params })
    .then((r) => r.data)

export const getPlatformError = (errorId: string) =>
  apiClient
    .get<PlatformErrorDetail>(`/api/v1/platform/errors/${errorId}`)
    .then((r) => r.data)

export const updatePlatformError = (
  errorId: string,
  data: { status: 'open' | 'acknowledged' | 'resolved'; resolution?: string },
) => apiClient.put(`/api/v1/platform/errors/${errorId}`, data).then((r) => r.data)

/** Measured, not reported. `unknown` means a probe could not run, and is never
 *  shown as healthy — a green light produced by a check that failed is worse
 *  than no light at all. */
export interface PlatformHealth {
  status: 'ok' | 'degraded' | 'down' | 'unknown'
  critical: number
  degraded: number
  unknown: number
  services: {
    service: string
    status: 'ok' | 'degraded' | 'down' | 'unknown'
    detail: string
  }[]
}

export const getPlatformHealth = () =>
  apiClient.get<PlatformHealth>('/api/v1/platform/health').then((r) => r.data)
