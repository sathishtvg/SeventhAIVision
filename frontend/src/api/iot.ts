import { apiClient } from './client'

export const SENSOR_TYPES = [
  { value: 'water_tank',    label: 'Water Tank',        icon: '💧', unit: '%'     },
  { value: 'pump',          label: 'Pump',              icon: '⚙️', unit: 'L/min' },
  { value: 'electricity',   label: 'Electricity',       icon: '⚡', unit: 'kW'    },
  { value: 'generator',     label: 'Generator',         icon: '🔋', unit: '%'     },
  { value: 'temperature',   label: 'Temperature',       icon: '🌡️', unit: '°C'    },
  { value: 'humidity',      label: 'Humidity',          icon: '💦', unit: '%'     },
  { value: 'air_quality',   label: 'Air Quality',       icon: '🌬️', unit: 'AQI'   },
  { value: 'water_quality', label: 'Water Quality',     icon: '🧪', unit: 'pH'    },
  { value: 'custom',        label: 'Custom',            icon: '📡', unit: ''      },
] as const

export interface IoTSensor {
  id: string
  tenant_id: string
  site_id: string | null
  name: string
  sensor_type: string
  unit: string | null
  location: string | null
  description: string | null
  is_active: boolean
  threshold_warning_low: number | null
  threshold_warning_high: number | null
  threshold_critical_low: number | null
  threshold_critical_high: number | null
  expected_interval_seconds: number
  last_reading_at: string | null
  last_reading_value: number | null
  current_status: string
  site_name: string | null
  open_alerts: number
  created_at: string
  updated_at: string
}

export interface IoTReading {
  id: string
  value: number
  recorded_at: string
}

export interface IoTAlert {
  id: string
  sensor_id: string
  alert_type: string
  severity: string
  value: number | null
  message: string | null
  status: string
  created_at: string
  acknowledged_at: string | null
  sensor_name: string
  sensor_type: string
  unit: string | null
  site_name: string | null
}

export interface IoTDashboard {
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

export interface SensorCreate {
  name: string
  sensor_type: string
  unit?: string
  location?: string
  description?: string
  site_id?: string
  threshold_warning_low?: number
  threshold_warning_high?: number
  threshold_critical_low?: number
  threshold_critical_high?: number
  expected_interval_seconds?: number
}

export const getIoTDashboard  = (site_id?: string): Promise<IoTDashboard>  =>
  apiClient.get('/api/v1/iot/dashboard', { params: site_id ? { site_id } : undefined }).then(r => r.data)

export const listIoTSensors   = (params?: { site_id?: string; sensor_type?: string }): Promise<IoTSensor[]> =>
  apiClient.get('/api/v1/iot/sensors', { params }).then(r => r.data)

export const createIoTSensor  = (body: SensorCreate): Promise<IoTSensor>   =>
  apiClient.post('/api/v1/iot/sensors', body).then(r => r.data)

export const updateIoTSensor  = (id: string, body: Partial<SensorCreate & { is_active: boolean }>): Promise<{ ok: boolean }> =>
  apiClient.put(`/api/v1/iot/sensors/${id}`, body).then(r => r.data)

export const getSensorReadings = (id: string, hours = 24): Promise<IoTReading[]> =>
  apiClient.get(`/api/v1/iot/sensors/${id}/readings`, { params: { hours } }).then(r => r.data)

export const ingestReading    = (id: string, value: number): Promise<{ status: string; alert_raised: boolean }> =>
  apiClient.post(`/api/v1/iot/sensors/${id}/readings`, { value }).then(r => r.data)

export const listIoTAlerts    = (params?: { status_filter?: string }): Promise<IoTAlert[]> =>
  apiClient.get('/api/v1/iot/alerts', { params }).then(r => r.data)

export const acknowledgeIoTAlert = (id: string): Promise<{ ok: boolean }> =>
  apiClient.put(`/api/v1/iot/alerts/${id}/acknowledge`).then(r => r.data)

export const resolveIoTAlert  = (id: string): Promise<{ ok: boolean }> =>
  apiClient.put(`/api/v1/iot/alerts/${id}/resolve`).then(r => r.data)
