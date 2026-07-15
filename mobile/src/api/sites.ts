import { apiClient } from './client'

export interface Site {
  id: string
  name: string
  address: string | null
  description: string | null
  is_active: boolean
}

export const getSites = () =>
  apiClient.get<Site[]>('/api/v1/sites').then((r) => r.data)
