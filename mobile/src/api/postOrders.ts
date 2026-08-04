/**
 * Post orders — the standing instructions for a site.
 *
 * This is the one module where mobile matters more than web: a guard needs the
 * site's SOP while standing at that site, not at a desk. Acknowledgement is the
 * compliance record that they read the current version, so `version` and
 * `acknowledged` travel together — an ack against an older version does not
 * count once the order is revised.
 *
 * Shapes mirror frontend/src/api/postOrders.ts.
 */
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

export const listPostOrders = (siteId?: string) =>
  apiClient
    .get<PostOrder[]>('/api/v1/post-orders', { params: siteId ? { site_id: siteId } : undefined })
    .then((r) => r.data)

export const getPostOrder = (id: string) =>
  apiClient.get<PostOrder>(`/api/v1/post-orders/${id}`).then((r) => r.data)

export const acknowledgePostOrder = (id: string) =>
  apiClient.post(`/api/v1/post-orders/${id}/acknowledge`).then((r) => r.data)
