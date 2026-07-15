import { apiClient } from './client'

export interface SOSResponse {
  sos_triggered: boolean
  entry_id: string
  occurred_at: string
  message: string
}

export const triggerSOS = (opts?: {
  latitude?: number
  longitude?: number
  site_id?: string
  description?: string
}) => apiClient.post<SOSResponse>('/api/v1/shifts/sos', opts ?? {}).then((r) => r.data)
