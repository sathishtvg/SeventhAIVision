import { apiClient } from './client'

export type ProtocolFieldType = 'string' | 'int' | 'bool' | 'select' | 'secret'

/** One configurable parameter, described well enough for the UI to render an
 *  input without knowing anything about the protocol. */
export interface ProtocolField {
  name: string
  label: string
  type: ProtocolFieldType
  required: boolean
  default: unknown
  help: string | null
  options: string[]
  min: number | null
  max: number | null
  /** Set when the value lands in a real table column rather than config JSONB.
   *  The form doesn't care — it's here because the API reports it. */
  column: string | null
}

export interface ProtocolSpec {
  key: string
  label: string
  description: string
  supports_close: boolean
  /** False for drivers written from a published spec but never run against
   *  real hardware — surfaced as a warning next to the protocol. */
  hardware_verified: boolean
  fields: ProtocolField[]
}

export interface DeviceFamily {
  family: string
  protocols: ProtocolSpec[]
}

export const listDeviceFamilies = () =>
  apiClient
    .get<{ families: DeviceFamily[] }>('/api/v1/device-protocols')
    .then((r) => r.data.families)

export const getDeviceFamily = (family: string) =>
  apiClient.get<DeviceFamily>(`/api/v1/device-protocols/${family}`).then((r) => r.data)
