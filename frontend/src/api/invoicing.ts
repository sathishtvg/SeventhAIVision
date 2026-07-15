import { apiClient } from './client'

export interface BillingClient {
  id: string
  name: string
  contact_name: string | null
  contact_email: string | null
  contact_phone: string | null
  billing_address: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface InvoiceLineItem {
  id: string
  site_id: string | null
  site_name: string
  regular_hours: number
  overtime_hours: number
  bill_rate: number
  regular_amount: number
  overtime_amount: number
  line_total: number
}

export interface Invoice {
  id: string
  client_id: string
  client_name: string
  invoice_number: string | null
  period_start: string
  period_end: string
  status: 'draft' | 'finalized' | 'paid' | 'void'
  subtotal: number
  tax_rate: number
  tax_amount: number
  total_amount: number
  due_date: string | null
  generated_at: string
  finalized_at: string | null
  paid_at: string | null
  voided_at: string | null
}

export interface InvoiceDetail extends Invoice {
  contact_name?: string | null
  contact_email?: string | null
  billing_address?: string | null
  line_items: InvoiceLineItem[]
  warnings?: string[]
}

export const listClients = () =>
  apiClient.get<BillingClient[]>('/api/v1/invoicing/clients').then((r) => r.data)

export const createClient = (data: {
  name: string; contact_name?: string; contact_email?: string; contact_phone?: string; billing_address?: string
}) => apiClient.post<{ id: string }>('/api/v1/invoicing/clients', data).then((r) => r.data)

export const updateClient = (id: string, data: Partial<BillingClient>) =>
  apiClient.put(`/api/v1/invoicing/clients/${id}`, data).then((r) => r.data)

export const deleteClient = (id: string) =>
  apiClient.delete(`/api/v1/invoicing/clients/${id}`).then((r) => r.data)

export const createInvoice = (data: {
  client_id: string; period_start: string; period_end: string; tax_rate?: number; due_date?: string
}) => apiClient.post<InvoiceDetail>('/api/v1/invoicing/invoices', data).then((r) => r.data)

export const listInvoices = (params?: { client_id?: string; invoice_status?: string }) =>
  apiClient.get<Invoice[]>('/api/v1/invoicing/invoices', { params }).then((r) => r.data)

export const getInvoice = (id: string) =>
  apiClient.get<InvoiceDetail>(`/api/v1/invoicing/invoices/${id}`).then((r) => r.data)

export const finalizeInvoice = (id: string) =>
  apiClient.put(`/api/v1/invoicing/invoices/${id}/finalize`).then((r) => r.data)

export const markInvoicePaid = (id: string) =>
  apiClient.put(`/api/v1/invoicing/invoices/${id}/mark-paid`).then((r) => r.data)

export const voidInvoice = (id: string) =>
  apiClient.put(`/api/v1/invoicing/invoices/${id}/void`).then((r) => r.data)

export const deleteInvoice = (id: string) =>
  apiClient.delete(`/api/v1/invoicing/invoices/${id}`).then((r) => r.data)

// Query-param-JWT URL — a PDF download link can't carry an Authorization
// header, mirrors payslipPdfUrl's pattern in api/payroll.ts.
export const invoicePdfUrl = (invoiceId: string, token: string | null) =>
  token ? `${apiClient.defaults.baseURL}/api/v1/invoicing/invoices/${invoiceId}/pdf?token=${token}` : null
