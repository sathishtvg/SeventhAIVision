import { apiClient } from './client'

/** The vendor's side of the ledger. Not /api/v1/billing, which is the Stripe
 *  integration — this is the invoice the vendor writes for a customer paying
 *  by bank transfer, which Stripe has no row for. */

export interface BillingModule {
  code: string
  name: string
  description: string | null
  /** included = free with the plan; flat = fixed per cycle; the rest scale. */
  billing_type: 'included' | 'flat' | 'per_camera' | 'per_site' | 'per_user'
  unit_price: string
  is_active: boolean
  sort_order: number
}

export interface BillingPlan {
  id: string
  name: string
  description: string | null
  billing_cycle: 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'custom'
  price_monthly: string | null
  price_yearly: string | null
  included_cameras: number
  included_sites: number
  included_users: number
  included_storage_gb: number
  price_per_camera: string
  price_per_site: string
  price_per_user: string
  price_per_gb: string
  max_cameras: number | null
  max_sites: number | null
  max_users: number | null
  support_level: string
  is_active: boolean
  subscribers: number
}

export interface QuoteLine {
  kind: string
  description: string
  quantity: string
  unit_price: string
  line_total: string
  module_code: string | null
}

export interface Quote {
  plan: string
  usage: { cameras: number; sites: number; users: number }
  lines: QuoteLine[]
  subtotal: string
  discount_amount: string
  tax_rate: string
  tax_amount: string
  total_amount: string
}

export interface PlatformInvoice {
  id: string
  tenant_id: string
  tenant_name: string
  invoice_number: string
  status: 'draft' | 'issued' | 'paid' | 'overdue' | 'cancelled' | 'credited'
  currency: string
  period_start: string | null
  period_end: string | null
  issued_at: string | null
  due_date: string | null
  paid_at: string | null
  subtotal: string
  discount_amount: string
  tax_rate: string
  tax_amount: string
  total_amount: string
  amount_paid: string
  notes: string | null
  credits_invoice_id: string | null
}

export interface InvoiceDetail extends PlatformInvoice {
  tenant_slug: string
  items: {
    id: string
    kind: string
    module_code: string | null
    description: string
    quantity: string
    unit_price: string
    line_total: string
  }[]
  payments: {
    id: string
    amount: string
    method: string
    reference: string | null
    received_at: string
    notes: string | null
  }[]
  amount_outstanding: string
}

export interface OutstandingRow {
  tenant_id: string
  tenant_name: string
  invoices: number
  billed: string
  paid: string
  overdue: number
  outstanding: string
}

export const getBillingModules = () =>
  apiClient.get<BillingModule[]>('/api/v1/platform/modules').then((r) => r.data)

export const updateBillingModule = (
  code: string,
  data: Partial<Pick<BillingModule, 'name' | 'billing_type' | 'unit_price' | 'is_active'>>,
) => apiClient.put<BillingModule>(`/api/v1/platform/modules/${code}`, data)
  .then((r) => r.data)

export const getBillingPlans = () =>
  apiClient.get<BillingPlan[]>('/api/v1/platform/plans').then((r) => r.data)

export const createBillingPlan = (data: Record<string, unknown>) =>
  apiClient.post<BillingPlan>('/api/v1/platform/plans', data).then((r) => r.data)

export const updateBillingPlan = (planId: string, data: Record<string, unknown>) =>
  apiClient.put<BillingPlan>(`/api/v1/platform/plans/${planId}`, data).then((r) => r.data)

export const previewPrice = (data: {
  tenant_id: string
  discount_amount?: string
  tax_rate?: string
}) => apiClient.post<Quote>('/api/v1/platform/pricing/preview', data).then((r) => r.data)

export const getInvoices = (params?: { tenant_id?: string; status?: string }) =>
  apiClient.get<PlatformInvoice[]>('/api/v1/platform/invoices', { params })
    .then((r) => r.data)

export const getInvoice = (invoiceId: string) =>
  apiClient.get<InvoiceDetail>(`/api/v1/platform/invoices/${invoiceId}`)
    .then((r) => r.data)

export const createInvoice = (data: {
  tenant_id: string
  period_start?: string
  period_end?: string
  due_date?: string
  discount_amount?: string
  tax_rate?: string
  notes?: string
}) => apiClient.post<{ id: string; invoice_number: string; total_amount: string }>(
  '/api/v1/platform/invoices', data).then((r) => r.data)

export const setInvoiceStatus = (invoiceId: string, status: 'issued' | 'cancelled') =>
  apiClient.post(`/api/v1/platform/invoices/${invoiceId}/status`, { status })
    .then((r) => r.data)

/** Negative for a refund. Marking an invoice paid is a consequence of the
 *  payments summing to the total, never a flag somebody sets. */
export const recordPayment = (invoiceId: string, data: {
  amount: string
  method?: string
  reference?: string
  notes?: string
}) => apiClient.post(`/api/v1/platform/invoices/${invoiceId}/payments`, data)
  .then((r) => r.data)

export const creditInvoice = (invoiceId: string) =>
  apiClient.post(`/api/v1/platform/invoices/${invoiceId}/credit`).then((r) => r.data)

export const getOutstanding = () =>
  apiClient.get<OutstandingRow[]>('/api/v1/platform/outstanding').then((r) => r.data)

// ── Notifications and policy ────────────────────────────────────────────────

export interface PlatformNotification {
  id: string
  kind: string
  severity: 'info' | 'warning' | 'critical'
  tenant_id: string | null
  tenant_name: string | null
  subject: string | null
  title: string
  detail: Record<string, unknown> | null
  created_at: string
  last_seen_at: string
  occurrences: number
  acknowledged_at: string | null
}

export interface PlatformSetting {
  key: string
  value: string
  description: string | null
  updated_at: string
}

export const getNotifications = (includeAcknowledged = false) =>
  apiClient.get<PlatformNotification[]>('/api/v1/platform/notifications', {
    params: { include_acknowledged: includeAcknowledged },
  }).then((r) => r.data)

export const acknowledgeNotification = (id: string) =>
  apiClient.post(`/api/v1/platform/notifications/${id}/acknowledge`).then((r) => r.data)

export const getPlatformSettings = () =>
  apiClient.get<PlatformSetting[]>('/api/v1/platform/settings').then((r) => r.data)

export const updatePlatformSetting = (key: string, value: string) =>
  apiClient.put<PlatformSetting>(`/api/v1/platform/settings/${key}`, { value })
    .then((r) => r.data)
