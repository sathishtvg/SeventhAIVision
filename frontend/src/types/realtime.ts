export type RealtimeEventType =
  | 'alert_created'
  | 'incident_created'
  | 'camera_status_changed'
  | 'attendance_status_changed'
  | 'roster_published'
  | 'visitor_entry_prompt'
  | 'visitor_exit_recorded'
  | 'barrier_command'
  | 'violation_created'
  | 'leave_status_changed'

export interface RealtimeEvent {
  schema_version: number
  event_type: RealtimeEventType
  tenant_id: string
  payload: Record<string, unknown>
  occurred_at: string
}
