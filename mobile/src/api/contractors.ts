import { apiClient } from './client'

export interface WorkPermit {
  id: string
  contractor_name: string
  company_name: string | null
  work_description: string
  location: string | null
  site_id: string | null
  status: 'pending' | 'approved' | 'active' | 'completed' | 'cancelled' | 'rejected'
  start_at: string
  end_at: string
  approved_by_user_id: string | null
  approver_name: string | null
  created_at: string
}

export interface Delivery {
  id: string
  tracking_number: string | null
  sender_name: string | null
  courier: string | null
  recipient_name: string
  description: string | null
  site_id: string | null
  status: 'pending' | 'received' | 'collected' | 'returned'
  expected_at: string | null
  received_at: string | null
  collected_at: string | null
  collected_by_name: string | null
  created_at: string
}

export const getWorkPermits = (params?: { status?: string; limit?: number }) =>
  apiClient
    .get<WorkPermit[]>('/api/v1/work-permits', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getDeliveries = (params?: { status?: string; limit?: number }) =>
  apiClient
    .get<Delivery[]>('/api/v1/deliveries', { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const approveWorkPermit = (permitId: string) =>
  apiClient.post(`/api/v1/work-permits/${permitId}/approve`, {}).then((r) => r.data)

export const collectDelivery = (deliveryId: string, collectedBy: string) =>
  apiClient
    .post(`/api/v1/deliveries/${deliveryId}/collect`, { collected_by: collectedBy })
    .then((r) => r.data)
