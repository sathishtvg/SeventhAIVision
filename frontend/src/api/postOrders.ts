import { apiClient } from './client'

export interface PostOrder {
  id: string
  site_id: string
  title: string
  body?: string
  category: string
  version: number
  is_active: boolean
  requires_acknowledgment: boolean
  site_name?: string
  created_by_name?: string | null
  acknowledged?: boolean
  created_at: string
  updated_at: string
}

export interface PostOrderAcks {
  post_order_id: string
  current_version: number
  acknowledged: { user_id: string; full_name: string | null; email: string; acknowledged_at: string }[]
  pending: { user_id: string; full_name: string | null; email: string }[]
}

export const POST_ORDER_CATEGORIES = [
  'general', 'emergency', 'access', 'patrol', 'equipment', 'contacts',
] as const

export const listPostOrders = (siteId?: string) =>
  apiClient
    .get<PostOrder[]>('/api/v1/post-orders', { params: siteId ? { site_id: siteId } : {} })
    .then((r) => r.data)

export const getPostOrder = (id: string) =>
  apiClient.get<PostOrder>(`/api/v1/post-orders/${id}`).then((r) => r.data)

export const createPostOrder = (data: {
  site_id: string
  title: string
  body: string
  category?: string
  requires_acknowledgment?: boolean
}) => apiClient.post<PostOrder>('/api/v1/post-orders', data).then((r) => r.data)

export const updatePostOrder = (id: string, data: Partial<{
  title: string
  body: string
  category: string
  requires_acknowledgment: boolean
  is_active: boolean
}>) => apiClient.put<PostOrder>(`/api/v1/post-orders/${id}`, data).then((r) => r.data)

export const deactivatePostOrder = (id: string) =>
  apiClient.delete(`/api/v1/post-orders/${id}`).then((r) => r.data)

export const acknowledgePostOrder = (id: string) =>
  apiClient.post(`/api/v1/post-orders/${id}/acknowledge`).then((r) => r.data)

export const getPostOrderAcks = (id: string) =>
  apiClient.get<PostOrderAcks>(`/api/v1/post-orders/${id}/acks`).then((r) => r.data)
