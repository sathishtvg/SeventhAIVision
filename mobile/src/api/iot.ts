/**
 * IoT sensors: what they are reading, and which are unhappy.
 *
 * THE OLD TYPES WERE INVENTED. This client asked for /iot/devices and
 * /iot/readings — neither exists — and described a "device" with a protocol, a
 * battery percentage and a firmware version. The server has sensors: a type, a
 * unit, thresholds, a last reading and a current status of normal, warning,
 * critical or offline. Nothing in the database has ever had a battery
 * percentage. Both calls answered 404 and the screen showed "No devices", which
 * looks like an estate with no sensors rather than a screen that cannot ask.
 *
 * Readings are per sensor, because that is how the server serves them —
 * /sensors/{id}/readings — and a single global feed never existed.
 */
import { apiClient } from './client'

const BASE = '/api/v1/iot'

export type SensorStatus = 'normal' | 'warning' | 'critical' | 'offline' | 'unknown'

export interface IoTSensor {
  id: string
  name: string
  sensor_type: string
  unit: string | null
  location: string | null
  description: string | null
  site_id: string | null
  site_name: string | null
  is_active: boolean
  current_status: SensorStatus
  last_reading_at: string | null
  last_reading_value: number | null
  threshold_warning_low: number | null
  threshold_warning_high: number | null
  threshold_critical_low: number | null
  threshold_critical_high: number | null
  expected_interval_seconds: number | null
  /** Open alerts against this sensor, counted by the server. */
  open_alerts: number
  created_at: string
}

export interface IoTReading {
  id: string
  value: number
  recorded_at: string
}

export interface IoTAlert {
  id: string
  sensor_id: string
  sensor_name?: string | null
  severity: string
  status: string
  message: string | null
  created_at: string
}

export interface IoTDashboard {
  total_sensors: number
  active_sensors: number
  summary: {
    total: number
    normal: number
    warning: number
    critical: number
    offline: number
    open_alerts: number
  }
  sensors: IoTSensor[]
}

export const getIoTDashboard = (params?: { site_id?: string }) =>
  apiClient.get<IoTDashboard>(`${BASE}/dashboard`, { params }).then((r) => r.data)

export const getIoTSensors = (params?: { site_id?: string; sensor_type?: string; status?: string }) =>
  apiClient.get<IoTSensor[]>(`${BASE}/sensors`, { params }).then((r) => r.data)

/** Recent readings for one sensor. There is no all-sensor feed on the server. */
export const getSensorReadings = (sensorId: string, params?: { limit?: number; hours?: number }) =>
  apiClient
    .get<IoTReading[]>(`${BASE}/sensors/${sensorId}/readings`, { params: { limit: 50, ...params } })
    .then((r) => r.data)

export const getIoTAlerts = (params?: { status?: string; limit?: number }) =>
  apiClient.get<IoTAlert[]>(`${BASE}/alerts`, { params }).then((r) => r.data)
