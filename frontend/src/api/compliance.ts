import { apiClient } from './client'

export interface TourSchedule {
  id: string
  route_id: string
  name: string
  description?: string
  recurrence: 'daily' | 'weekdays' | 'weekends' | 'custom'
  days_of_week?: number[]
  scheduled_time: string
  window_minutes: number
  assigned_guard_user_id?: string
  assigned_guard_name?: string
  route_name?: string
  site_name?: string
  is_active: boolean
  created_at: string
  total_30d?: number
  completed_30d?: number
  compliance_rate_30d?: number
  avg_score_30d?: number
}

export interface TourOccurrence {
  id: string
  schedule_id: string
  session_id?: string
  scheduled_at: string
  window_end: string
  status: 'pending' | 'completed' | 'incomplete' | 'missed' | 'late'
  compliance_score?: number
  missed_checkpoints?: number
  notes?: string
  schedule_name?: string
  assigned_guard_name?: string
  route_name?: string
  site_name?: string
  session_started_at?: string
  scanned_checkpoints?: number
  total_checkpoints?: number
}

export interface ComplianceDashboard {
  tours_today: number
  completed_today: number
  missed_today: number
  late_today: number
  incomplete_today: number
  total_7d: number
  completed_7d: number
  compliance_rate_7d?: number
  compliance_rate_30d?: number
  avg_score_7d?: number
  active_schedules: number
  daily_trend: Array<{
    day: string
    total: number
    completed: number
    missed: number
    partial: number
  }>
  recent_missed: Array<{
    id: string
    scheduled_at: string
    status: string
    schedule_name: string
    route_name: string
    assigned_guard?: string
  }>
}

export interface ComplianceReport {
  date_from: string
  date_to: string
  summary: {
    total_resolved: number
    completed: number
    missed: number
    late: number
    incomplete: number
    pending: number
    avg_score?: number
    compliance_rate?: number
  }
  by_guard: Array<{
    guard_id?: string
    guard_name?: string
    total: number
    completed: number
    missed: number
    avg_score?: number
    compliance_rate?: number
  }>
  by_route: Array<{
    route_id: string
    route_name: string
    site_name?: string
    total: number
    completed: number
    missed: number
    avg_score?: number
    compliance_rate?: number
  }>
  daily_trend: Array<{
    day: string
    total: number
    completed: number
    missed: number
    avg_score?: number
    compliance_rate?: number
  }>
}

export async function getComplianceDashboard(): Promise<ComplianceDashboard> {
  const r = await apiClient.get('/api/v1/compliance/dashboard')
  return r.data
}

export async function listSchedules(isActive?: boolean): Promise<TourSchedule[]> {
  const params = isActive !== undefined ? { is_active: isActive } : {}
  const r = await apiClient.get('/api/v1/compliance/schedules', { params })
  return r.data
}

export async function createSchedule(data: {
  route_id: string
  name: string
  description?: string
  recurrence?: string
  days_of_week?: number[]
  scheduled_time: string
  window_minutes?: number
  assigned_guard_user_id?: string
}): Promise<TourSchedule> {
  const r = await apiClient.post('/api/v1/compliance/schedules', data)
  return r.data
}

export async function updateSchedule(id: string, data: Partial<TourSchedule>): Promise<TourSchedule> {
  const r = await apiClient.put(`/api/v1/compliance/schedules/${id}`, data)
  return r.data
}

export async function generateOccurrences(scheduleId: string, days = 7): Promise<{ generated: number; skipped_existing: number }> {
  const r = await apiClient.post(`/api/v1/compliance/schedules/${scheduleId}/generate?days=${days}`)
  return r.data
}

export async function listOccurrences(params?: {
  date_from?: string
  date_to?: string
  schedule_id?: string
  status?: string
  guard_id?: string
  limit?: number
}): Promise<TourOccurrence[]> {
  const r = await apiClient.get('/api/v1/compliance/occurrences', { params })
  return r.data
}

export async function resolveOccurrence(id: string, data: {
  session_id?: string
  status: string
  compliance_score?: number
  notes?: string
}): Promise<{ id: string; status: string; compliance_score?: number }> {
  const r = await apiClient.put(`/api/v1/compliance/occurrences/${id}/resolve`, data)
  return r.data
}

export async function getComplianceReport(params: {
  date_from: string
  date_to: string
  guard_id?: string
  site_id?: string
}): Promise<ComplianceReport> {
  const r = await apiClient.get('/api/v1/compliance/report', { params })
  return r.data
}
