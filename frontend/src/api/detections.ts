import type {
  Detection, LprEvent, FaceEvent, IntrusionEvent,
  PpeEvent, CrowdEvent, FireSmokeEvent, WeaponEvent, BehaviorEvent,
  TamperingEvent, AbandonedObjectEvent, FallEvent,
} from '@/types/api'
import { apiClient } from './client'

export const getDetections = (moduleType?: string, limit = 50) =>
  apiClient
    .get<{ items: Detection[] }>('/api/v1/detections', { params: { module_type: moduleType, limit } })
    .then((r) => r.data.items)

export const getLprEvents = (plateNumber?: string, limit = 50) =>
  apiClient
    .get<{ items: LprEvent[] }>('/api/v1/detections/lpr-events', { params: { plate_number: plateNumber, limit } })
    .then((r) => r.data.items)

export const getFaceEvents = (limit = 50) =>
  apiClient.get<{ items: FaceEvent[] }>('/api/v1/detections/face-events', { params: { limit } }).then((r) => r.data.items)

export const getIntrusionEvents = (limit = 50) =>
  apiClient.get<{ items: IntrusionEvent[] }>('/api/v1/detections/intrusion-events', { params: { limit } }).then((r) => r.data.items)

export const getPpeEvents = (limit = 50) =>
  apiClient.get<PpeEvent[]>('/api/v1/detections/ppe-events', { params: { limit } }).then((r) => r.data)

export const getCrowdEvents = (zoneId?: string, limit = 50) =>
  apiClient
    .get<CrowdEvent[]>('/api/v1/detections/crowd-events', { params: { zone_id: zoneId, limit } })
    .then((r) => r.data)

export const getFireSmokeEvents = (detectionType?: 'fire' | 'smoke', limit = 50) =>
  apiClient
    .get<FireSmokeEvent[]>('/api/v1/detections/fire-smoke-events', { params: { detection_type: detectionType, limit } })
    .then((r) => r.data)

export const getWeaponEvents = (weaponType?: string, limit = 50) =>
  apiClient
    .get<WeaponEvent[]>('/api/v1/detections/weapon-events', { params: { weapon_type: weaponType, limit } })
    .then((r) => r.data)

export const getBehaviorEvents = (behaviorType?: string, limit = 50) =>
  apiClient
    .get<BehaviorEvent[]>('/api/v1/detections/behavior-events', { params: { behavior_type: behaviorType, limit } })
    .then((r) => r.data)

export const getTamperingEvents = (tamperingType?: string, limit = 50) =>
  apiClient
    .get<TamperingEvent[]>('/api/v1/advanced-detections/tampering', { params: { tampering_type: tamperingType, limit } })
    .then((r) => r.data)

export const getAbandonedEvents = (minDwellSeconds?: number, limit = 50) =>
  apiClient
    .get<AbandonedObjectEvent[]>('/api/v1/advanced-detections/abandoned', { params: { min_dwell_seconds: minDwellSeconds, limit } })
    .then((r) => r.data)

export const getFallEvents = (minConfidence?: number, limit = 50) =>
  apiClient
    .get<FallEvent[]>('/api/v1/advanced-detections/falls', { params: { min_confidence: minConfidence, limit } })
    .then((r) => r.data)
