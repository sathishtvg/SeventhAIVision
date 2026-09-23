/**
 * Sensor ordering.
 *
 * The estate list is long and the screen shows the top of it. A critical sensor
 * sorted below a healthy one is a reading nobody scrolls to.
 */
import { sensorsByUrgency } from './IoTScreen'
import type { IoTSensor } from '@/api/iot'

const sensor = (name: string, current_status: IoTSensor['current_status']): IoTSensor => ({
  id: name, name, sensor_type: 'temperature', unit: 'C', location: null, description: null,
  site_id: null, site_name: null, is_active: true, current_status,
  last_reading_at: null, last_reading_value: null,
  threshold_warning_low: null, threshold_warning_high: null,
  threshold_critical_low: null, threshold_critical_high: null,
  expected_interval_seconds: null, open_alerts: 0, created_at: '',
})

describe('sensorsByUrgency', () => {
  it('puts critical first and normal last', () => {
    const out = sensorsByUrgency([
      sensor('a-normal', 'normal'), sensor('b-critical', 'critical'),
      sensor('c-warning', 'warning'), sensor('d-offline', 'offline'),
    ])
    expect(out.map((s) => s.current_status)).toEqual(['critical', 'warning', 'offline', 'normal'])
  })

  it('sorts by name within a status, so the list is stable to read', () => {
    const out = sensorsByUrgency([sensor('zulu', 'warning'), sensor('alpha', 'warning')])
    expect(out.map((s) => s.name)).toEqual(['alpha', 'zulu'])
  })

  it('does not mutate what it was given', () => {
    const input = [sensor('a', 'normal'), sensor('b', 'critical')]
    sensorsByUrgency(input)
    expect(input.map((s) => s.name)).toEqual(['a', 'b'])
  })

  it('handles an empty estate', () => {
    expect(sensorsByUrgency([])).toEqual([])
  })
})
