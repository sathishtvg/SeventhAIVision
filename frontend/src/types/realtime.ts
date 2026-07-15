export type RealtimeEventType =
  | 'alert_created'
  | 'incident_created'
  | 'camera_status_changed'
  | 'attendance_status_changed'
  | 'roster_published'

export interface RealtimeEvent {
  schema_version: number
  event_type: RealtimeEventType
  tenant_id: string
  payload: Record<string, unknown>
  occurred_at: string
}
