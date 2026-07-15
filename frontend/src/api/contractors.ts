import { apiClient } from './client'

export interface Contractor {
  id: string
  tenant_id: string
  company_name: string
  registration_number?: string
  contact_name?: string
  contact_phone?: string
  contact_email?: string
  address?: string
  specialization?: string
  vetting_status: 'pending' | 'approved' | 'suspended' | 'rejected'
  vetting_notes?: string
  vetted_by_name?: string
  vetted_at?: string
  is_active: boolean
  created_at: string
  active_permits?: number
  accreditation_count?: number
}

export interface ContractorAccreditation {
  id: string
  contractor_id: string
  document_type: string
  document_number?: string
  issued_by?: string
  issued_at?: string
  expires_at?: string
  notes?: string
  created_at: string
}

export interface WorkPermit {
  id: string
  contractor_id: string
  site_id?: string
  permit_number?: string
  work_description: string
  work_type?: string
  requested_by_name?: string
  requested_by_email?: string
  workers_count: number
  vehicles_count: number
  start_at: string
  end_at: string
  status: 'pending' | 'approved' | 'rejected' | 'active' | 'completed' | 'cancelled'
  approved_by_name?: string
  approved_at?: string
  rejection_reason?: string
  safety_briefing_done: boolean
  company_name?: string
  site_name?: string
  created_at: string
}

export interface Delivery {
  id: string
  site_id?: string
  tracking_number?: string
  carrier?: string
  sender_name?: string
  sender_company?: string
  recipient_name: string
  recipient_department?: string
  description?: string
  expected_at?: string
  received_at?: string
  received_by_name?: string
  collected_at?: string
  collected_by_name?: string
  status: 'pending' | 'received' | 'collected' | 'rejected' | 'returned'
  rejection_reason?: string
  notes?: string
  site_name?: string
  created_at: string
}

export interface ContractorsDashboard {
  total_contractors: number
  pending_vetting: number
  approved_contractors: number
  pending_permits: number
  active_permits: number
  pending_deliveries: number
  received_deliveries: number
}

export async function getContractorsDashboard(): Promise<ContractorsDashboard> {
  const r = await apiClient.get('/api/v1/contractors-dashboard')
  return r.data
}

export async function listContractors(vettingStatus?: string): Promise<Contractor[]> {
  const r = await apiClient.get('/api/v1/contractors', { params: vettingStatus ? { vetting_status: vettingStatus } : {} })
  return r.data
}

export async function createContractor(data: Partial<Contractor>): Promise<Contractor> {
  const r = await apiClient.post('/api/v1/contractors', data)
  return r.data
}

export async function vetContractor(id: string, vetting_status: string, vetting_notes?: string) {
  const r = await apiClient.put(`/api/v1/contractors/${id}/vet`, { vetting_status, vetting_notes })
  return r.data
}

export async function addAccreditation(contractorId: string, data: Partial<ContractorAccreditation>) {
  const r = await apiClient.post(`/api/v1/contractors/${contractorId}/accreditations`, data)
  return r.data
}

export interface WorkPermitsPage { items: WorkPermit[]; has_more: boolean }

export async function listWorkPermits(params?: {
  status?: string
  contractor_id?: string
  site_id?: string
  limit?: number
  offset?: number
}): Promise<WorkPermitsPage> {
  const r = await apiClient.get('/api/v1/work-permits', { params })
  return r.data
}

export async function createWorkPermit(data: Partial<WorkPermit>): Promise<WorkPermit> {
  const r = await apiClient.post('/api/v1/work-permits', data)
  return r.data
}

export async function approvePermit(id: string) {
  const r = await apiClient.put(`/api/v1/work-permits/${id}/approve`)
  return r.data
}

export async function rejectPermit(id: string, reason?: string) {
  const r = await apiClient.put(`/api/v1/work-permits/${id}/reject`, { reason })
  return r.data
}

export async function completePermit(id: string) {
  const r = await apiClient.put(`/api/v1/work-permits/${id}/complete`)
  return r.data
}

export async function listDeliveries(params?: { status?: string; site_id?: string }): Promise<Delivery[]> {
  const r = await apiClient.get('/api/v1/deliveries', { params })
  return r.data
}

export async function createDelivery(data: Partial<Delivery>): Promise<Delivery> {
  const r = await apiClient.post('/api/v1/deliveries', data)
  return r.data
}

export async function receiveDelivery(id: string, notes?: string) {
  const r = await apiClient.put(`/api/v1/deliveries/${id}/receive`, { notes })
  return r.data
}

export async function collectDelivery(id: string, collected_by_name: string) {
  const r = await apiClient.put(`/api/v1/deliveries/${id}/collect`, { collected_by_name })
  return r.data
}

export async function rejectDelivery(id: string, reason?: string) {
  const r = await apiClient.put(`/api/v1/deliveries/${id}/reject`, { reason })
  return r.data
}
