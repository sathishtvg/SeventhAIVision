import { apiClient } from './client'

export interface ReportSchedule {
  id: string
  name: string
  report_type: string
  frequency: string
  day_of_week: number | null
  day_of_month: number | null
  hour_utc: number
  site_id: string | null
  site_name: string | null
  delivery_method: string
  recipients: string[]
  webhook_url: string | null
  is_active: boolean
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
}

export interface ReportDelivery {
  id: string
  status: string
  delivered_at: string | null
  error_message: string | null
  report_period_start: string | null
  report_period_end: string | null
  created_at: string
}

export interface ScheduleCreate {
  name: string
  report_type?: string
  frequency?: string
  day_of_week?: number | null
  day_of_month?: number | null
  hour_utc?: number
  site_id?: string | null
  delivery_method?: string
  recipients?: string[]
  webhook_url?: string | null
  is_active?: boolean
}

export interface ScheduleUpdate {
  name?: string
  frequency?: string
  day_of_week?: number | null
  day_of_month?: number | null
  hour_utc?: number
  delivery_method?: string
  recipients?: string[]
  webhook_url?: string | null
  is_active?: boolean
}

export const REPORT_TYPES = ['site_summary', 'dob', 'incident_summary'] as const
export const FREQUENCIES = ['daily', 'weekly', 'monthly'] as const
export const DELIVERY_METHODS = ['email', 'webhook'] as const
export const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export const listSchedules = () =>
  apiClient.get<ReportSchedule[]>('/api/v1/scheduled-reports').then((r) => r.data)

export const createSchedule = (data: ScheduleCreate) =>
  apiClient.post<ReportSchedule>('/api/v1/scheduled-reports', data).then((r) => r.data)

export const updateSchedule = (id: string, data: ScheduleUpdate) =>
  apiClient.put<ReportSchedule>(`/api/v1/scheduled-reports/${id}`, data).then((r) => r.data)

export const deleteSchedule = (id: string) =>
  apiClient.delete(`/api/v1/scheduled-reports/${id}`).then((r) => r.data)

export const listDeliveries = (scheduleId: string) =>
  apiClient.get<ReportDelivery[]>(`/api/v1/scheduled-reports/${scheduleId}/deliveries`).then((r) => r.data)

export const runNow = (scheduleId: string) =>
  apiClient.post<{ delivery_id: string; status: string }>(
    `/api/v1/scheduled-reports/${scheduleId}/run-now`
  ).then((r) => r.data)
